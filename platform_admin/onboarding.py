"""Institution onboarding: first administrator, welcome email and checklist.

The platform admin area reuses the ordinary account services (invitations,
temporary passwords, sign-up links and branding). Every write made here is
written to the append-only platform audit log with the actor and institution.
Passwords, invitation tokens and one-time links are returned to the caller for
a single on-screen display; they are never put in an audit row or an email log.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from accounts.user_admin import (
    _invitation_link,
    create_invitation,
    generate_temporary_password,
    normalized_email,
    resend_invitation,
    reset_password,
    unique_username,
)
from core.audit import log_action
from core.email_types import INSTITUTION_APPLICATION_APPROVED, WELCOME
from core.notifications import (
    absolute_url,
    notify,
    queue_message,
    render_email_html,
    send_message,
)
from core.roles import ensure_roles
from core.tenancy import use_institution

from .models import InstitutionOnboarding
from .services import log_platform_action

User = get_user_model()

SUPPORT_PHONE = "+263774573765"
SUPPORT_EMAIL = "vanhuvaonejeffrey@gmail.com"

GETTING_STARTED = (
    "Upload your logo.",
    "Set the academic year and term.",
    "Add your staff and students.",
    "Create sign-up links for them.",
)


@dataclass
class AdministratorProvision:
    """The result of creating one institution administrator.

    ``secret`` is either a temporary password or a one-time invitation link.
    The view puts it in the session for exactly one display and the service
    itself never stores or logs it.
    """

    user: User
    access_mode: str
    secret: str
    invitation: object = None
    email_status: str = ""
    email_failed: bool = False


def institution_users(institution):
    """All accounts of one institution, never platform staff."""
    return User.objects.filter(institution=institution, is_platform_admin=False)


def administrators(institution):
    """Administrator accounts of one institution only."""
    people = institution_users(institution)
    return (
        people.filter(role=User.Role.ADMIN)
        | people.filter(primary_role__code="institution_admin")
    ).distinct()


def admin_checklist(institution):
    """The seven onboarding steps, with their current state and next link."""
    from academics.models import AcademicYear
    from platform_admin.models import Subscription

    onboarding = InstitutionOnboarding.for_institution(institution)
    people = institution_users(institution)
    admins = administrators(institution)
    signed_in = bool(onboarding.first_admin_login_at) or admins.filter(
        last_login__isnull=False
    ).exists()
    logo = bool(institution.logo) or bool((institution.settings or {}).get("logo"))
    year = AcademicYear.all_objects.filter(institution=institution).exists()
    modules = institution.modules.filter(enabled=True).exists()
    other_users = people.exclude(pk__in=admins.values_list("pk", flat=True)).exists()
    plan = Subscription.objects.filter(institution=institution).exists()

    def step(key, label, done, detail, url_name=None, url_args=(institution.pk,)):
        return {
            "key": key,
            "label": label,
            "done": bool(done),
            "detail": detail,
            "url": reverse(url_name, args=url_args) if url_name else "",
        }

    items = [
        step(
            "administrator",
            "Administrator created",
            admins.exists(),
            f"{admins.count()} administrator account(s)",
            "platform_admin:institution_admins",
        ),
        step(
            "signed_in",
            "Administrator signed in",
            signed_in,
            "The first administrator has signed in." if signed_in else "Not yet signed in.",
        ),
        step(
            "logo",
            "Logo uploaded",
            logo,
            "Branding is in place." if logo else "Upload the institution logo.",
        ),
        step(
            "academic_year",
            "Academic year set up",
            year,
            "An academic year exists." if year else "No academic year yet.",
        ),
        step(
            "modules",
            "Modules chosen",
            modules,
            "At least one module is switched on." if modules else "Choose the modules.",
            "platform_admin:institution_modules",
        ),
        step(
            "first_users",
            "First users added",
            other_users,
            "Staff or student accounts exist." if other_users else "No staff or students yet.",
        ),
        step(
            "plan",
            "Plan assigned",
            plan,
            "A subscription is in place." if plan else "Assign a plan.",
            "platform_admin:subscriptions",
            [],
        ),
    ]
    done = sum(item["done"] for item in items)
    return {
        "items": items,
        "done": done,
        "total": len(items),
        "percent": round(done * 100 / len(items)),
        "missing": [item["label"] for item in items if not item["done"]],
    }


def _institution_notice(institution, admin, actor=None):
    """Tell the institution (not the new administrator) about the new account."""
    if not institution.contact_email:
        return None
    body = (
        f"Hello {institution.name},\n\n"
        f"A platform administrator added {admin.get_full_name() or admin.username} "
        f"as an administrator for your institution.\n\n"
        f"Username: {admin.username}\n"
        "No password is included in this email. If the person cannot sign in, "
        "ask the platform administrator to send a new invitation or reset the password.\n\n"
        f"Support: {SUPPORT_PHONE} / {SUPPORT_EMAIL}\n"
    )
    message = queue_message(
        "email",
        institution.contact_email,
        body,
        subject=f"A new administrator was added to {institution.name}",
        institution=institution,
        related_object=admin,
    )
    return send_message(message)


def send_welcome_email(
    institution,
    admin,
    *,
    request=None,
    invitation_link="",
    force=False,
    application=None,
    plan=None,
):
    """Send the branded welcome email once, unless an explicit resend."""
    onboarding = InstitutionOnboarding.for_institution(institution)
    if onboarding.welcome_email_sent_at and not force:
        return None
    if not admin.email:
        return None

    login_url = absolute_url(reverse("institution_login", args=[institution.slug]))
    body = (
        f"Hello {admin.get_full_name() or admin.username},\n\n"
        f"Welcome to EduSphere. {institution.name} is ready for you.\n\n"
        f"Sign in here: {login_url}\n"
    )
    if invitation_link:
        body += f"Choose your password here (single use, 72 hours): {invitation_link}\n"
    else:
        body += "Use the temporary password given to your platform administrator, then change it at first sign-in.\n"
    body += (
        "\nGetting started:\n"
        + "".join(f"{index}. {step}\n" for index, step in enumerate(GETTING_STARTED, 1))
        + f"\nNeed help? Call/WhatsApp {SUPPORT_PHONE} or email {SUPPORT_EMAIL}.\n"
    )
    html_body = render_email_html(
        "emails/platform_application_approved.html",
        {
            "institution": institution,
            "user": admin,
            "application": application,
            "plan": plan,
            "login_url": login_url,
            "invitation_link": invitation_link,
            "support_phone": SUPPORT_PHONE,
            "support_email": SUPPORT_EMAIL,
            "getting_started": GETTING_STARTED,
            "welcome_only": application is None,
        },
    )
    email_type = INSTITUTION_APPLICATION_APPROVED if application else WELCOME
    message = queue_message(
        "email",
        admin.email,
        body,
        subject=f"Welcome to EduSphere - set up {institution.name}",
        institution=institution,
        related_object=admin,
        html_body=html_body,
        email_type=email_type,
    )
    send_message(message)
    now = timezone.now()
    onboarding.welcome_email_sent_at = onboarding.welcome_email_sent_at or now
    onboarding.welcome_email_count += 1
    onboarding.save(update_fields=["welcome_email_sent_at", "welcome_email_count", "updated"])
    log_platform_action(
        request.user if request and getattr(request.user, "is_authenticated", False) else None,
        "institution.welcome_email_sent",
        institution=institution,
        record_type="Institution",
        record_id=institution.pk,
        detail={"recipient": admin.email, "resend": bool(force)},
        request=request,
    )
    return message


def resend_welcome_email(institution, admin, actor=None, request=None):
    """Explicit resend: always sends, even if the welcome email went before."""
    message = send_welcome_email(
        institution, admin, request=request, force=True,
        invitation_link=_current_invitation_link(admin),
    )
    log_platform_action(
        actor,
        "institution.welcome_email_resent",
        institution=institution,
        record_type="Institution",
        record_id=institution.pk,
        request=request,
    )
    return message


def _current_invitation_link(admin):
    invitation = admin.invitations.filter(used_at__isnull=True).order_by("-created").first()
    if invitation is None or not invitation.is_open:
        return ""
    return _invitation_link(invitation)


@transaction.atomic
def add_administrator(
    institution,
    *,
    full_name,
    email,
    phone="",
    access_mode="invitation",
    actor=None,
    request=None,
):
    """Create an institution administrator in one of the two access modes."""
    if access_mode not in ("invitation", "temporary_password"):
        raise ValidationError("Choose how this administrator gets access.")
    full_name = (full_name or "").strip()
    if not full_name:
        raise ValidationError("Enter the administrator's full name.")
    email = normalized_email(email)
    if access_mode == "invitation" and not email:
        raise ValidationError("An invitation needs an email address.")
    if email and institution_users(institution).filter(email__iexact=email).exists():
        raise ValidationError("That email address is already used at this institution.")

    with use_institution(institution):
        role = ensure_roles(institution).get("institution_admin")
        if role is None:
            raise ValidationError("The institution administrator role is missing.")
        parts = full_name.split(None, 1)
        first_name, last_name = parts[0][:150], (parts[1] if len(parts) > 1 else "")[:150]
        username = unique_username(email.split("@")[0] if email else first_name, email)
        user = User(
            username=username,
            email=email,
            first_name=first_name,
            last_name=last_name,
            phone=(phone or "").strip()[:30],
            role=User.Role.ADMIN,
            primary_role=role,
            institution=institution,
            is_staff=True,
            is_active=True,
            is_platform_admin=False,
            must_change_password=access_mode == "temporary_password",
        )

        invitation = None
        secret = ""
        email_failed = False
        email_status = ""
        if access_mode == "invitation":
            user.set_unusable_password()
            user.save()
            invitation = create_invitation(
                user, actor=actor, request=request, send=False
            )
            secret = _invitation_link(invitation)
            from accounts.user_admin import _invitation_body, _invitation_html

            message = queue_message(
                "email",
                user.email,
                _invitation_body(user, invitation, request),
                subject=f"Your {institution.name} account",
                institution=institution,
                related_object=invitation,
                html_body=_invitation_html(user, invitation, request),
            )
            send_message(message)
            email_status = message.status
            email_failed = email_status != "sent"
        else:
            secret = generate_temporary_password()
            user.set_password(secret)
            user.save()

        log_action(
            actor,
            "institution.admin_created",
            institution=institution,
            instance=user,
            after={"username": user.username, "access_mode": access_mode},
            request=request,
        )
        log_platform_action(
            actor,
            "institution.admin_created",
            institution=institution,
            record_type="User",
            record_id=user.pk,
            detail={"username": user.username, "access_mode": access_mode},
            request=request,
        )
        onboarding = InstitutionOnboarding.for_institution(institution)
        if onboarding.first_admin_created_at is None:
            onboarding.first_admin_created_at = timezone.now()
            onboarding.save(update_fields=["first_admin_created_at", "updated"])
        welcome_already_sent = onboarding.welcome_email_sent_at is not None

        if access_mode == "temporary_password":
            from accounts.user_admin import send_account_created_notice

            notice = send_account_created_notice(user)
            email_status = notice.status if notice is not None else ""
            email_failed = bool(notice is not None and notice.status != "sent")

        # Every new administrator receives the branded welcome email. In the
        # invitation flow it carries the one-time set-password link; in the
        # temporary-password flow it explains that no password was emailed.
        welcome = send_welcome_email(
            institution, user, request=request,
            invitation_link=secret if access_mode == "invitation" else "",
            force=welcome_already_sent,
        )
        if welcome is not None and welcome.status != "sent":
            email_failed = True
            email_status = welcome.status
        _institution_notice(institution, user, actor=actor)
        notify(
            actor,
            f"Administrator added to {institution.name}",
            f"{user.get_full_name() or user.username} is now an administrator.",
            link=reverse("platform_admin:institution_admins", args=[institution.pk]),
            institution=institution,
        )

    return AdministratorProvision(
        user=user,
        access_mode=access_mode,
        secret=secret,
        invitation=invitation,
        email_status=email_status,
        email_failed=email_failed,
    )


def mark_admin_signed_in(user, request=None):
    """Record the first sign-in of an institution administrator."""
    institution = getattr(user, "institution", None)
    if institution is None or not administrators(institution).filter(pk=user.pk).exists():
        return
    onboarding = InstitutionOnboarding.for_institution(institution)
    if onboarding.first_admin_login_at is not None:
        return
    onboarding.first_admin_login_at = timezone.now()
    onboarding.save(update_fields=["first_admin_login_at", "updated"])
    log_platform_action(
        None,
        "institution.first_admin_signed_in",
        institution=institution,
        record_type="User",
        record_id=user.pk,
        detail={"username": user.username},
        request=request,
    )


def institution_admin_action(institution, user, action, *, actor=None, request=None):
    """Platform side of administrator lifecycle actions, with an audit row."""
    if not administrators(institution).filter(pk=user.pk).exists():
        raise ValidationError("That account does not belong to this institution.")

    secret = ""
    before = {"is_active": user.is_active, "locked": bool(user.locked_until)}
    if action == "resend_invitation":
        secret = _current_invitation_link(user)
        resend_invitation(user, actor=None, request=request)
        secret = _current_invitation_link(user)
    elif action == "reset_password":
        secret = reset_password(user, actor=None, request=request)
    elif action == "unlock":
        from accounts.user_admin import unlock_user

        unlock_user(user, actor=None, request=request)
    elif action in ("deactivate", "remove"):
        from accounts.user_admin import deactivate_user, ensure_not_last_administrator

        ensure_not_last_administrator(user)
        deactivate_user(user, actor=None, request=request, reason=f"platform:{action}")
    elif action == "activate":
        from accounts.user_admin import reactivate_user

        reactivate_user(user, actor=None, request=request)
    else:
        raise ValidationError("Unknown administrator action.")

    log_platform_action(
        actor,
        f"institution.admin_{action}",
        institution=institution,
        record_type="User",
        record_id=user.pk,
        before=before,
        after={"is_active": user.is_active, "locked": bool(user.locked_until)},
        detail={"username": user.username},
        request=request,
    )
    return secret


def email_login_link(institution, admin, *, actor=None, request=None):
    """Email the branded institution sign-in link to the administrator."""
    login_url = absolute_url(reverse("institution_login", args=[institution.slug]))
    body = (
        f"Hello {admin.get_full_name() or admin.username},\n\n"
        f"Sign in to {institution.name} here: {login_url}\n\n"
        f"Support: {SUPPORT_PHONE} / {SUPPORT_EMAIL}\n"
    )
    message = queue_message(
        "email",
        admin.email,
        body,
        subject=f"Sign in to {institution.name}",
        institution=institution,
        related_object=institution,
        html_body=render_email_html(
            "emails/institution_login_link.html",
            {"institution": institution, "login_url": login_url,
             "support_phone": SUPPORT_PHONE, "support_email": SUPPORT_EMAIL},
        ),
    )
    send_message(message)
    log_platform_action(
        actor,
        "institution.login_link_emailed",
        institution=institution,
        record_type="User",
        record_id=admin.pk,
        detail={"recipient": admin.email},
        request=request,
    )
    return message
