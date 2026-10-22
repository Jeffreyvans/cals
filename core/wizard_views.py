"""The setup wizard screens.

A new institution is set up in six short steps. The answers are kept in the
session until the last step, where they become real rows. Only somebody who
may create institutions can open the wizard.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render

from core import wizard
from core.access import user_has_code
from core.modules import ALL_MODULES, module_label
from core.tenant_defaults import DEFAULT_MODULES

CREATE_INSTITUTIONS = "admin.create_institutions"


def may_create_institutions(user):
    """True when the user may run the setup wizard."""
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    return user_has_code(user, CREATE_INSTITUTIONS)


def _state(request):
    """The answers collected so far, with the empty ones filled in."""
    state = wizard.get_state(request)
    fresh = wizard.default_state()
    for key, value in fresh.items():
        state.setdefault(key, value)
    return state


def _merged_values(state, values, step):
    """What the form should show: the saved answers, with any new ones on top."""
    saved = {"details": state.get("details"), "year": state.get("year"),
             "admin": state.get("admin")}
    merged = dict(saved.get(step) or {})
    if step == "admin":
        merged.pop("password", None)
        merged.pop("password_confirm", None)
    incoming = (values or {}).get(step)
    if isinstance(incoming, dict):
        for key, value in incoming.items():
            if step == "admin" and key in ("password", "password_confirm"):
                continue
            if value != "" or key not in merged:
                merged[key] = value
    return merged


def _step_context(request, state, step, errors=None, values=None):
    """Everything the wizard template needs."""
    from core.models import Institution

    index = wizard.STEP_NAMES.index(step)
    details = state.get("details") or {}
    chosen = list(state.get("modules") or [])
    if values and values.get("modules") is not None:
        chosen = list(values["modules"])
    if not chosen:
        chosen = list(DEFAULT_MODULES.get(details.get("type"), []))

    if step == "grading":
        grading = values.get("grading") if values and values.get("grading") is not None \
            else (state.get("grading") or wizard.DEFAULT_GRADES)
    else:
        grading = state.get("grading") or wizard.DEFAULT_GRADES

    if step == "demo":
        demo = values.get("demo") if values and values.get("demo") is not None \
            else (state.get("demo") or {"enabled": False, "prefix": ""})
    else:
        demo = state.get("demo") or {"enabled": False, "prefix": ""}

    module_rows = [
        {
            "code": code,
            "label": module_label(code),
            "on": code in chosen,
        }
        for code in ALL_MODULES
    ]
    return {
        "steps": wizard.STEPS,
        "step": step,
        "step_index": index,
        "step_count": len(wizard.STEPS),
        "step_label": dict(wizard.STEPS)[step],
        "errors": errors or [],
        "form": _merged_values(state, values, step),
        "module_rows": module_rows,
        "institution_types": Institution.Type.choices,
        "grading": grading,
        "demo": demo,
        "details": details,
    }


def _read_details(request):
    return {
        "name": request.POST.get("name", ""),
        "slug": request.POST.get("slug", ""),
        "type": request.POST.get("type", ""),
        "currency": request.POST.get("currency", ""),
        "timezone": request.POST.get("timezone", ""),
        "language": request.POST.get("language", ""),
        "address": request.POST.get("address", ""),
        "contact_email": request.POST.get("contact_email", ""),
        "contact_phone": request.POST.get("contact_phone", ""),
        "primary_color": request.POST.get("primary_color", ""),
        "accent_color": request.POST.get("accent_color", ""),
    }


def _read_year(request):
    return {
        "year_name": request.POST.get("year_name", ""),
        "year_start": request.POST.get("year_start", ""),
        "year_end": request.POST.get("year_end", ""),
        "term_name": request.POST.get("term_name", ""),
        "term_start": request.POST.get("term_start", ""),
        "term_end": request.POST.get("term_end", ""),
    }


def _read_grading(request):
    letters = request.POST.getlist("letter")
    min_marks = request.POST.getlist("min_mark")
    points = request.POST.getlist("points")
    rows = []
    for index, letter in enumerate(letters):
        rows.append({
            "letter": letter,
            "min_mark": min_marks[index] if index < len(min_marks) else "",
            "points": points[index] if index < len(points) else "",
        })
    return rows


def _read_admin(request):
    return {
        "username": request.POST.get("username", ""),
        "first_name": request.POST.get("first_name", ""),
        "last_name": request.POST.get("last_name", ""),
        "email": request.POST.get("email", ""),
        "password": request.POST.get("password", ""),
        "password_confirm": request.POST.get("password_confirm", ""),
    }


def _read_demo(request):
    return {
        "enabled": request.POST.get("enabled") == "on",
        "prefix": request.POST.get("prefix", ""),
    }


@login_required
def setup_wizard(request):
    """Walk through the six steps and create the institution at the end."""
    if not may_create_institutions(request.user):
        raise PermissionDenied("Only a platform administrator may create institutions.")

    state = _state(request)
    step = wizard.current_step(request)

    if request.method == "POST":
        action = request.POST.get("action", "next")

        if action == "back":
            previous = wizard.previous_step(step)
            state["step"] = previous or step
            wizard.set_state(request, state)
            return redirect("setup_wizard")

        values = {
            "details": _read_details(request),
            "year": _read_year(request),
            "modules": request.POST.getlist("modules"),
            "grading": _read_grading(request),
            "admin": _read_admin(request),
            "demo": _read_demo(request),
        }

        if step == "details":
            clean, errors = wizard.clean_details(values["details"])
        elif step == "year":
            clean, errors = wizard.clean_year(values["year"])
        elif step == "modules":
            clean, errors = wizard.clean_modules(
                values["modules"], (state.get("details") or {}).get("type"))
        elif step == "grading":
            clean, errors = wizard.clean_grading(values["grading"])
        elif step == "admin":
            clean, errors = wizard.clean_admin(values["admin"])
        else:
            clean, errors = wizard.clean_demo(values["demo"])

        if errors:
            context = _step_context(request, state, step, errors=errors, values=values)
            return render(request, "core/setup_wizard.html", context)

        state[step] = clean
        if step == "modules":
            state["modules"] = clean["modules"]
        if step == "grading":
            state["grading"] = clean["grading"]

        if step == "demo":
            # The last step creates the institution.
            try:
                wizard.validate_state(state)
            except ValidationError as exc:
                problems = exc.messages if hasattr(exc, "messages") else [str(exc)]
                context = _step_context(request, state, step, errors=problems,
                                        values=values)
                return render(request, "core/setup_wizard.html", context)
            institution = wizard.build_institution(state)
            wizard.clear_state(request)
            request.session["setup_created"] = institution.pk
            request.session.modified = True
            messages.success(request, f"{institution.name} is ready.")
            return redirect("setup_done")

        following = wizard.next_step(step)
        state["step"] = following or step
        wizard.set_state(request, state)
        return redirect("setup_wizard")

    context = _step_context(request, state, step)
    return render(request, "core/setup_wizard.html", context)


@login_required
def setup_done(request):
    """What was built, and how to sign in."""
    if not may_create_institutions(request.user):
        raise PermissionDenied("Only a platform administrator may create institutions.")
    from core.models import Institution

    pk = request.session.get("setup_created")
    institution = Institution.objects.filter(pk=pk).first() if pk else None
    if institution is None:
        return redirect("setup_wizard")
    settings_row = institution.settings or {}
    context = {
        "inst": institution,
        "demo_enabled": bool(settings_row.get("demo_data")),
        "logins": settings_row.get("demo_logins") or [],
    }
    return render(request, "core/setup_done.html", context)
