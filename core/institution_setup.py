"""Guided first-sign-in setup for an institution administrator.

The platform admin creates the institution and its first administrator. When
that person signs in for the first time they are taken through five short,
institution-scoped steps: choose a password, upload a logo, check the details,
set the academic year and create a sign-up link for their first users.

This deliberately reuses ``templates/core/setup_wizard.html`` and the pure
validation helpers in :mod:`core.wizard`, but it edits the *existing*
institution. It never grants an institution admin the platform-only
``admin.create_institutions`` permission that guards the creation wizard.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone

from core import wizard
from core.audit import log_action

SESSION_KEY = "institution_setup_step"

STEPS = [
    ("institution_password", "Choose a password"),
    ("institution_logo", "Upload your logo"),
    ("institution_details", "Institution details"),
    ("institution_year", "Academic year"),
    ("institution_users", "Add your first users"),
]
STEP_KEYS = [key for key, _label in STEPS]

User = get_user_model()


def onboarding_row(institution):
    from platform_admin.models import InstitutionOnboarding

    return InstitutionOnboarding.for_institution(institution)


def queue_setup_after_login(user, request):
    """Queue the one-shot guided-setup redirect after a *real* form login.

    This is called from the login views' ``form_valid`` only. Authentication
    signals also fire for ``Client.force_login()`` and ``Client.login()``, so
    keeping this out of the signal stops ordinary tests and background code
    from being redirected. The check below runs before Django logs the user
    in, so ``first_admin_login_at`` is still empty on a genuine first login.
    """
    if request is None or not getattr(user, "is_authenticated", False):
        return
    if getattr(user, "is_platform_admin", False):
        return
    institution = getattr(user, "institution", None)
    if institution is None or not hasattr(request, "session"):
        return
    from platform_admin.onboarding import administrators

    if not administrators(institution).filter(pk=user.pk).exists():
        return
    row = onboarding_row(institution)
    if row.first_admin_login_at is None and row.setup_completed_at is None:
        request.session["needs_institution_setup"] = True


def mark_setup_started(user):
    """Remember that the first administrator opened the guided setup."""
    institution = getattr(user, "institution", None)
    if institution is None:
        return
    row = onboarding_row(institution)
    if row.setup_started_at is None:
        row.setup_started_at = timezone.now()
        row.save(update_fields=["setup_started_at", "updated"])
        log_action(user, "institution.setup_started", institution, request=None)


def mark_setup_completed(institution, user):
    row = onboarding_row(institution)
    if row.setup_completed_at is None:
        row.setup_completed_at = timezone.now()
        row.save(update_fields=["setup_completed_at", "updated"])
    log_action(user, "institution.setup_completed", institution, request=None)


def _institution_for(request):
    """Only the administrator of the current institution may set it up."""
    user = request.user
    if not getattr(user, "is_authenticated", False) or getattr(user, "is_platform_admin", False):
        raise PermissionDenied("Only an institution administrator can do this.")
    institution = getattr(request, "institution", None) or getattr(user, "institution", None)
    if institution is None:
        raise Http404("No institution is linked to this account.")
    is_admin = user.role == User.Role.ADMIN or (
        getattr(user.primary_role, "code", "") == "institution_admin"
    )
    if not is_admin or institution.id != user.institution_id:
        raise PermissionDenied("Only an institution administrator can do this.")
    return institution


def _step_context(request, institution, step, errors=None, values=None, created_url=""):
    index = STEP_KEYS.index(step)
    values = values or {}
    form = dict(values.get(step) or {})
    if step == "institution_details":
        for key in (
            "tagline", "about", "contact_email", "contact_phone", "website",
            "address", "primary_color", "accent_color",
        ):
            form.setdefault(key, (institution.settings or {}).get(key, getattr(institution, key, "")))
    if step == "institution_users":
        form.setdefault("kind", "any")
    preview = ""
    if step == "institution_logo" and (institution.logo or (institution.settings or {}).get("logo")):
        from core.logo import institution_logo_url

        preview = institution_logo_url(institution, 160)
    return {
        "setup_mode": True,
        "institution": institution,
        "steps": STEPS,
        "step": step,
        "step_index": index,
        "step_count": len(STEPS),
        "step_label": dict(STEPS)[step],
        "errors": errors or [],
        "form": form,
        "logo_preview": preview,
        "created_url": created_url,
        "finish_label": "Finish setup" if step == STEPS[-1][0] else "Save and continue",
    }


def _advance(request, step):
    index = STEP_KEYS.index(step)
    following = STEP_KEYS[index + 1] if index + 1 < len(STEP_KEYS) else step
    request.session[SESSION_KEY] = following
    request.session.modified = True
    return redirect("institution_setup")


def _save_password(request, user, errors):
    values = {
        "password": request.POST.get("password", ""),
        "password_confirm": request.POST.get("password_confirm", ""),
    }
    if request.POST.get("action") == "skip":
        if not user.has_usable_password():
            errors.append("Choose a password before continuing.")
        return values
    password = values["password"]
    if password != values["password_confirm"]:
        errors.append("The two passwords do not match.")
    elif len(password) < 8:
        errors.append("Use at least eight characters.")
    else:
        from accounts.validators import validate_strong_password

        checks = [lambda: validate_password(password, user),
                  lambda: validate_strong_password(password, user.username, user.email)]
        for check in checks:
            try:
                check()
            except ValidationError as exc:
                errors.extend(exc.messages if hasattr(exc, "messages") else [str(exc)])
    if not errors:
        user.set_password(password)
        user.must_change_password = False
        user.save(update_fields=["password", "must_change_password"])
        # Keep the newly authenticated session valid after the hash changed.
        from django.contrib.auth import update_session_auth_hash

        update_session_auth_hash(request, user)
        log_action(user, "institution.setup_password", user.institution, request=request)
    return values


def _save_logo(request, institution, errors):
    upload = request.FILES.get("logo")
    if upload is None:
        return {}
    from core.logo import save_logo

    try:
        save_logo(institution, upload)
    except ValidationError as exc:
        errors.extend(exc.messages)
        return {}
    log_action(request.user, "institution.setup_logo", institution, request=request)
    return {}


def _save_details(request, institution):
    fields = {
        "tagline": ("tagline", 200),
        "about": ("about", 600),
        "contact_email": ("contact_email", 200),
        "contact_phone": ("contact_phone", 30),
        "website": ("website", 200),
        "address": ("address", 2000),
        "primary_color": ("primary_color", 20),
        "accent_color": ("accent_color", 20),
    }
    values = {}
    for key, (column, limit) in fields.items():
        value = (request.POST.get(key, "") or "").strip()[:limit]
        values[key] = value
        setattr(institution, column, value)
    institution.save(update_fields=[column for column, _limit in fields.values()])
    log_action(request.user, "institution.setup_details", institution, request=request)
    return values


def _save_year(request, institution, errors):
    from academics.models import AcademicYear, Term
    from core.tenancy import use_institution

    values = {
        "year_name": request.POST.get("year_name", ""),
        "year_start": request.POST.get("year_start", ""),
        "year_end": request.POST.get("year_end", ""),
        "term_name": request.POST.get("term_name", ""),
        "term_start": request.POST.get("term_start", ""),
        "term_end": request.POST.get("term_end", ""),
    }
    if AcademicYear.all_objects.filter(institution=institution).exists():
        return values
    clean, problems = wizard.clean_year(values)
    errors.extend(problems)
    if not errors:
        with use_institution(institution):
            year = AcademicYear.objects.create(
                institution=institution,
                name=clean["year_name"],
                start_date=clean["year_start"],
                end_date=clean["year_end"],
                is_current=True,
            )
            Term.objects.create(
                institution=institution,
                academic_year=year,
                name=clean["term_name"],
                start_date=clean["term_start"],
                end_date=clean["term_end"],
                is_current=True,
            )
        log_action(request.user, "institution.setup_year", institution, request=request)
    return values


def _save_users(request, institution, errors):
    from accounts.signup_links import create_signup_link

    label = (request.POST.get("label", "") or "").strip()
    kind = request.POST.get("kind", "any")
    if kind not in ("student", "staff", "any"):
        errors.append("Choose who the sign-up link is for.")
        return {"label": label, "kind": kind}, ""
    link = create_signup_link(
        institution,
        label=label,
        kind=kind,
        actor=request.user,
        request=request,
    )
    row = onboarding_row(institution)
    if row.signup_links_created_at is None:
        row.signup_links_created_at = timezone.now()
        row.save(update_fields=["signup_links_created_at", "updated"])
    return {"label": label, "kind": kind}, link.url()


@login_required
def institution_setup(request):
    """The five-step setup shown after the first administrator signs in."""
    institution = _institution_for(request)
    step = request.session.get(SESSION_KEY, STEPS[0][0])
    if step not in STEP_KEYS:
        step = STEPS[0][0]
    mark_setup_started(request.user)

    errors = []
    values = {}
    created_url = ""
    if request.method == "POST":
        action = request.POST.get("action", "next")
        if action == "back":
            index = STEP_KEYS.index(step)
            if index > 0:
                request.session[SESSION_KEY] = STEP_KEYS[index - 1]
                request.session.modified = True
            return redirect("institution_setup")
        if step == STEPS[-1][0]:
            if action == "next":
                # Create the link but stay on this page so it can be copied
                # once. A second "Finish setup" post completes the flow.
                values, created_url = _save_users(request, institution, errors)
            elif action in {"finish", "skip"}:
                mark_setup_completed(institution, request.user)
                request.session.pop(SESSION_KEY, None)
                request.session.modified = True
                if action == "finish":
                    messages.success(request, "Setup complete. Your institution is ready.")
                return redirect("dashboard")
        elif step == "institution_password":
            values = _save_password(request, request.user, errors)
            if not errors:
                return _advance(request, step)
        elif step == "institution_logo":
            values = _save_logo(request, institution, errors)
            if not errors:
                return _advance(request, step)
        elif step == "institution_details":
            values = _save_details(request, institution)
            return _advance(request, step)
        elif step == "institution_year":
            values = _save_year(request, institution, errors)
            if not errors:
                return _advance(request, step)

    context = _step_context(request, institution, step, errors, values, created_url)
    return render(request, "core/setup_wizard.html", context)
