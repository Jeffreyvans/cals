"""Business rules for applications, consent and platform access.

The views are deliberately thin. Everything that changes money, consent or an
institution's data goes through a service here so the same checks apply from a
web page, a test or a management command.
"""

from __future__ import annotations

import secrets
import string
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from core.audit import log_action
from core.email_types import (
    INSTITUTION_APPLICATION_APPROVED,
    INSTITUTION_APPLICATION_MORE_INFO,
    INSTITUTION_APPLICATION_RECEIVED,
    INSTITUTION_APPLICATION_REJECTED,
    PLATFORM_ACCESS_DECISION,
    PLATFORM_ACCESS_REQUEST,
)
from core.models import Institution, InstitutionModule
from core.modules import ALL_MODULES
from core.notifications import notify, queue_message, render_email_html, send_message
from core.roles import ensure_roles
from core.tenancy import use_institution
from core.tenant_defaults import DEFAULT_MODULES

from .legal import DATA_PROCESSING_STATEMENT, DATA_PROCESSING_VERSION
from .models import (
    ACCESS_CATEGORY_CODES,
    HIGH_PROTECTION_CATEGORIES,
    AccessRequest,
    ApplicationDocument,
    ApplicationEvent,
    ApplicationMessage,
    DataAccessGrant,
    DataConsentLog,
    InstitutionApplication,
    Plan,
    PlatformAuditLog,
    PlatformRole,
    PlatformStaff,
    Subscription,
)
from .permissions import PLATFORM_ROLE_PRESETS, has_code
from .twofa import ready_for_sensitive_action

User = get_user_model()

MAX_GRANT_HOURS = 24 * 7
DEFAULT_GRANT_HOURS = 24
EMERGENCY_HOURS = 2
GRANT_IDLE_MINUTES = 15
REQUEST_LIFETIME_DAYS = 7
RATE_WINDOW = 60 * 60


# ---------- small helpers ----------


def client_ip(request):
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return request.META.get("REMOTE_ADDR")


def platform_staff_for(user):
    """The active PlatformStaff row for a user, or None. Cached on the user."""
    if not getattr(user, "is_authenticated", False):
        return None
    if hasattr(user, "_platform_staff_cache"):
        return user._platform_staff_cache
    staff = (
        PlatformStaff.objects.select_related("role")
        .filter(user_id=user.pk, is_active=True)
        .first()
    )
    user._platform_staff_cache = staff
    return staff


def is_platform_staff(user):
    return platform_staff_for(user) is not None


def is_main_platform_admin(staff):
    return bool(staff is not None and staff.is_active and staff.is_main_admin)


def require_platform_permission(user, *codes, main_admin=False):
    staff = platform_staff_for(user)
    if staff is None:
        raise PermissionDenied("This area is for EduSphere platform staff.")
    if main_admin and not staff.is_main_admin:
        raise PermissionDenied("Only the platform main administrator may do this.")
    if codes and not any(has_code(staff, code) for code in codes):
        raise PermissionDenied("Your platform role does not allow this action.")
    return staff


def log_platform_action(
    actor,
    action,
    *,
    institution=None,
    grant=None,
    record_type="",
    record_id="",
    detail=None,
    before=None,
    after=None,
    request=None,
):
    """Write one append-only platform audit row."""
    user = actor if getattr(actor, "is_authenticated", False) else None
    return PlatformAuditLog.objects.create(
        actor=user,
        action=action,
        institution=institution,
        grant=grant,
        record_type=record_type,
        record_id=str(record_id or "")[:80],
        ip_address=client_ip(request),
        user_agent=(request.META.get("HTTP_USER_AGENT", "")[:300] if request else ""),
        detail=detail or {},
        before=before,
        after=after,
    )


def active_grant_for(user, institution):
    return (
        DataAccessGrant.objects.select_related("institution")
        .filter(
            platform_admin=user,
            institution=institution,
            revoked_at__isnull=True,
            expired_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
        .order_by("-created")
        .first()
    )


def active_grants_for(user):
    return DataAccessGrant.objects.select_related("institution").filter(
        platform_admin=user,
        revoked_at__isnull=True,
        expired_at__isnull=True,
        expires_at__gt=timezone.now(),
    )


def grant_for_request(user, institution, request=None):
    """The active grant plus an idle-session check."""
    grant = active_grant_for(user, institution)
    if grant is None:
        return None
    now = timezone.now()
    if grant.last_seen_at and now - grant.last_seen_at > timedelta(minutes=GRANT_IDLE_MINUTES):
        return None
    grant.last_seen_at = now
    grant.save(update_fields=["last_seen_at"])
    return grant


def record_granted_change(
    actor, institution, record_type, record_id, before, after, request=None, grant=None
):
    """Audit a change made while a consent grant is in force."""
    grant = grant or active_grant_for(actor, institution)
    if grant is None:
        return None
    return log_platform_action(
        actor,
        "platform.change",
        institution=institution,
        grant=grant,
        record_type=record_type,
        record_id=record_id,
        before=before,
        after=after,
        request=request,
    )


# ---------- seeding ----------


def ensure_platform_roles():
    roles = {}
    for spec in PLATFORM_ROLE_PRESETS:
        role, _ = PlatformRole.objects.get_or_create(
            code=spec["code"],
            defaults={
                "name": spec["name"],
                "description": spec["description"],
                "permissions": spec["permissions"],
                "is_system": True,
            },
        )
        roles[role.code] = role
    return roles


DEFAULT_PLANS = [
    {
        "code": "starter",
        "name": "Starter",
        "description": "Core records, fees and the parent portal.",
        "monthly_price": "49.00",
        "annual_price": "490.00",
        "trial_days": 14,
        "max_students": 500,
        "max_staff": 60,
        "modules": ["core_academics", "finance", "parent_portal", "library"],
        "ordering": 1,
    },
    {
        "code": "standard",
        "name": "Standard",
        "description": "Everything a growing school needs, including exams and health.",
        "monthly_price": "99.00",
        "annual_price": "990.00",
        "trial_days": 21,
        "max_students": 2000,
        "max_staff": 250,
        "modules": [
            "core_academics", "finance", "admissions", "parent_portal", "lms",
            "exams", "library", "health", "counselling", "sports",
        ],
        "ordering": 2,
    },
    {
        "code": "college",
        "name": "College",
        "description": "Programmes, research, labs and the full campus suite.",
        "monthly_price": "199.00",
        "annual_price": "1990.00",
        "trial_days": 30,
        "max_students": 20000,
        "max_staff": 2000,
        "modules": [
            "core_academics", "finance", "admissions", "parent_portal", "lms",
            "exams", "library", "health", "counselling", "student_affairs",
            "sports", "boarding", "transport", "catering", "careers_attachment",
            "research", "labs", "ict_helpdesk", "hr", "payroll",
            "procurement_stores", "estates", "security", "compliance",
            "marketing", "governance", "api", "sso",
        ],
        "ordering": 3,
    },
]


def ensure_default_plans():
    created = []
    for spec in DEFAULT_PLANS:
        plan, was_created = Plan.objects.get_or_create(
            code=spec["code"],
            defaults={key: value for key, value in spec.items() if key != "code"},
        )
        if was_created:
            created.append(plan)
    return created


def ensure_platform_staff(user, role_code="main_admin", is_main_admin=True):
    roles = ensure_platform_roles()
    staff, _ = PlatformStaff.objects.get_or_create(
        user=user,
        defaults={
            "role": roles.get(role_code),
            "is_main_admin": is_main_admin,
            "is_active": True,
        },
    )
    changed = False
    desired_role = roles.get(role_code)
    if desired_role is not None and staff.role_id != desired_role.pk:
        # "Ensure" means the account really holds the requested role, even if
        # an older row (or a previous deployment) gave it a different one.
        staff.role = desired_role
        changed = True
    if is_main_admin and not staff.is_main_admin:
        staff.is_main_admin = True
        changed = True
    if is_main_admin and not staff.is_active:
        staff.is_active = True
        changed = True
    if changed:
        staff.save(update_fields=["role", "is_main_admin", "is_active"])
    return staff


# ---------- application helpers ----------


def application_reference():
    year = timezone.now().year
    alphabet = string.ascii_uppercase + string.digits
    while True:
        reference = f"APP-{year}-" + "".join(secrets.choice(alphabet) for _ in range(6))
        if not InstitutionApplication.objects.filter(reference=reference).exists():
            return reference


def application_rate_key(request):
    return f"edusphere.platform.apply.{client_ip(request) or 'unknown'}"


def application_rate_limited(request, limit=5):
    from django.core.cache import cache

    return cache.get(application_rate_key(request), 0) >= limit


def record_application_attempt(request):
    from django.core.cache import cache

    key = application_rate_key(request)
    cache.set(key, cache.get(key, 0) + 1, RATE_WINDOW)


def application_status_url(application, request=None):
    from core.notifications import absolute_url

    return absolute_url(reverse(
        "platform_admin:apply_status", args=[application.applicant_token]))


def notify_platform_admins(title, body="", link="", category="platform"):
    users = [
        row.user
        for row in PlatformStaff.objects.select_related("user").filter(
            is_active=True, user__is_active=True
        )
    ]
    for user in users:
        notify(user, title, body, link=link, category=category)
    return users


def _application_email(application, subject, template, body, extra=None,
                       email_type=INSTITUTION_APPLICATION_RECEIVED):
    context = {
        "application": application,
        "institution_name": application.institution_name,
        "reference": application.reference,
        "status_url": application_status_url(application),
    }
    context.update(extra or {})
    html_body = render_email_html(template, context)
    message = queue_message(
        "email",
        application.email,
        body,
        subject=subject,
        related_object=application,
        html_body=html_body,
        email_type=email_type,
    )
    send_message(message)
    return message


def create_application(data, files=None, request=None):
    """Create one application and its optional documents. Never says if it exists."""
    files = files or {}
    application = InstitutionApplication.objects.create(
        reference=application_reference(),
        institution_name=str(data.get("institution_name", "")).strip()[:200],
        institution_type=data.get("institution_type") or Institution.Type.COLLEGE,
        country=str(data.get("country", "Zimbabwe")).strip()[:100] or "Zimbabwe",
        town=str(data.get("town", "")).strip()[:100],
        registration_number=str(data.get("registration_number", "")).strip()[:100],
        expected_students=int(data.get("expected_students") or 0),
        expected_staff=int(data.get("expected_staff") or 0),
        contact_person=str(data.get("contact_person", "")).strip()[:150],
        position=str(data.get("position", "")).strip()[:120],
        phone=str(data.get("phone", "")).strip()[:40],
        email=str(data.get("email", "")).strip().lower()[:254],
        website=str(data.get("website", "")).strip()[:200],
        preferred_plan=data.get("preferred_plan"),
        modules=[code for code in (data.get("modules") or []) if code in ALL_MODULES],
        agreed_to_terms=bool(data.get("agreed_to_terms")),
        consent_statement_version=DATA_PROCESSING_VERSION,
        consent_accepted_at=timezone.now() if data.get("agreed_to_terms") else None,
        submitted_ip=client_ip(request),
    )
    from .uploads import sanitise_upload

    for kind, key in (
        (ApplicationDocument.Kind.REGISTRATION_CERTIFICATE, "registration_certificate"),
        (ApplicationDocument.Kind.AUTHORISATION_LETTER, "authorisation_letter"),
    ):
        upload = files.get(key)
        if not upload:
            continue
        safe = sanitise_upload(upload, stem=key)
        document = ApplicationDocument(
            application=application,
            kind=kind,
            original_name=getattr(upload, "name", "")[:255],
            content_type=safe.content_type,
            size=safe.size,
            sha256=safe.sha256,
        )
        document.file.save(safe.name, safe.content, save=False)
        document.save()

    ApplicationEvent.objects.create(
        application=application,
        action="submitted",
        note="Application received from the public form.",
        data={"ip": client_ip(request) or ""},
    )
    subject = f"We received your EduSphere application ({application.reference})"
    body = (
        f"Hello {application.contact_person},\n\n"
        f"Thank you for applying to use EduSphere for {application.institution_name}.\n"
        f"Your reference is {application.reference}.\n\n"
        f"Follow the progress of your application here:\n"
        f"{application_status_url(application, request)}\n\n"
        "We will email you when there is an update. You never need to send a "
        "password by email.\n"
    )
    _application_email(
        application,
        subject,
        "emails/platform_application_received.html",
        body,
        email_type=INSTITUTION_APPLICATION_RECEIVED,
    )
    notify_platform_admins(
        "New institution application",
        f"{application.institution_name} ({application.reference}) applied to use EduSphere.",
        link=reverse("platform_admin:application_detail", args=[application.pk]),
        category="applications",
    )
    log_platform_action(
        None,
        "application.submitted",
        record_type="InstitutionApplication",
        record_id=application.pk,
        detail={"reference": application.reference, "type": application.institution_type},
        request=request,
    )
    return application


def _event(application, action, actor=None, note="", data=None):
    return ApplicationEvent.objects.create(
        application=application,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        note=note,
        data=data or {},
    )


def assign_application(application, reviewer, actor, request=None):
    application.assigned_to = reviewer
    if application.status == InstitutionApplication.Status.SUBMITTED:
        application.status = InstitutionApplication.Status.UNDER_REVIEW
    application.save(update_fields=["assigned_to", "status", "updated"])
    _event(application, "assigned", actor, f"Assigned to {reviewer.get_full_name() or reviewer.username}.")
    log_platform_action(
        actor, "application.assigned", record_type="InstitutionApplication",
        record_id=application.pk, detail={"reviewer_id": reviewer.pk}, request=request,
    )
    return application


def add_internal_note(application, actor, note, request=None):
    note = (note or "").strip()
    if not note:
        raise ValidationError("Write a note first.")
    application.internal_notes = (
        (application.internal_notes + "\n\n" if application.internal_notes else "") + note
    )
    application.save(update_fields=["internal_notes", "updated"])
    _event(application, "internal_note", actor, note)
    log_platform_action(
        actor, "application.note_added", record_type="InstitutionApplication",
        record_id=application.pk, request=request,
    )
    return application


def request_more_information(application, actor, message, request=None):
    message = (message or "").strip()
    if len(message) < 10:
        raise ValidationError("Explain what information you need.")
    if not application.is_open:
        raise ValidationError("This application is closed.")
    application.status = InstitutionApplication.Status.MORE_INFORMATION_NEEDED
    application.more_information_message = message
    application.save(update_fields=["status", "more_information_message", "updated"])
    ApplicationMessage.objects.create(
        application=application, author_kind=ApplicationMessage.AuthorKind.STAFF,
        author=actor, body=message,
    )
    _event(application, "more_information_requested", actor, message)
    _application_email(
        application,
        f"More information needed for {application.reference}",
        "emails/platform_application_more_info.html",
        (
            f"Hello {application.contact_person},\n\n"
            f"We need a little more information about {application.institution_name}.\n\n"
            f"{message}\n\n"
            f"Reply securely here:\n{application_status_url(application, request)}\n"
        ),
        {"message": message},
        email_type=INSTITUTION_APPLICATION_MORE_INFO,
    )
    log_platform_action(
        actor, "application.more_information", record_type="InstitutionApplication",
        record_id=application.pk, request=request,
    )
    return application


def applicant_reply(application, message, request=None):
    message = (message or "").strip()
    if not application.can_reply:
        raise ValidationError("This application is not waiting for a reply.")
    if len(message) < 5:
        raise ValidationError("Write a reply first.")
    ApplicationMessage.objects.create(
        application=application, author_kind=ApplicationMessage.AuthorKind.APPLICANT,
        body=message,
    )
    application.status = InstitutionApplication.Status.UNDER_REVIEW
    application.save(update_fields=["status", "updated"])
    _event(application, "applicant_replied", None, message[:500])
    notify_platform_admins(
        "Applicant replied",
        f"{application.institution_name} ({application.reference}) sent more information.",
        link=reverse("platform_admin:application_detail", args=[application.pk]),
        category="applications",
    )
    log_platform_action(
        None, "application.applicant_replied", record_type="InstitutionApplication",
        record_id=application.pk, request=request,
    )
    return application


def withdraw_application(application, request=None):
    if not application.is_open:
        raise ValidationError("This application can no longer be withdrawn.")
    application.status = InstitutionApplication.Status.WITHDRAWN
    application.save(update_fields=["status", "updated"])
    _event(application, "withdrawn", None, "The applicant withdrew the application.")
    log_platform_action(
        None, "application.withdrawn", record_type="InstitutionApplication",
        record_id=application.pk, request=request,
    )
    return application


def reject_application(application, actor, reason, request=None):
    reason = (reason or "").strip()
    if len(reason) < 10:
        raise ValidationError("Give a reason of at least 10 characters.")
    if application.status == InstitutionApplication.Status.APPROVED:
        raise ValidationError("An approved application cannot be rejected.")
    if application.status == InstitutionApplication.Status.REJECTED:
        raise ValidationError("This application has already been rejected.")
    application.status = InstitutionApplication.Status.REJECTED
    application.decision_reason = reason
    application.decided_by = actor
    application.decided_at = timezone.now()
    application.save(
        update_fields=["status", "decision_reason", "decided_by", "decided_at", "updated"]
    )
    _event(application, "rejected", actor, reason, {"reason": reason})
    _application_email(
        application,
        f"Update on your EduSphere application ({application.reference})",
        "emails/platform_application_rejected.html",
        (
            f"Hello {application.contact_person},\n\n"
            f"We are sorry that we cannot approve the application for "
            f"{application.institution_name} at this time.\n\n"
            f"Reason: {reason}\n\n"
            "If you think this is a mistake, reply to this message or contact "
            "EduSphere support.\n"
        ),
        {"reason": reason},
        email_type=INSTITUTION_APPLICATION_REJECTED,
    )
    log_platform_action(
        actor, "application.rejected", record_type="InstitutionApplication",
        record_id=application.pk, detail={"reason": reason}, request=request,
    )
    return application


def _unique_slug(name):
    from core.slugs import unique_slug

    return unique_slug(name)


def _unique_username(email, first_name):
    seed = (email.split("@")[0] if email else first_name) or "admin"
    seed = "".join(ch for ch in seed.lower() if ch.isalnum() or ch in "._-")[:40] or "admin"
    candidate = seed
    counter = 1
    while User.objects.filter(username__iexact=candidate).exists():
        counter += 1
        candidate = f"{seed[:35]}{counter}"
    return candidate


def _split_name(full_name):
    parts = [part for part in (full_name or "").split() if part]
    if not parts:
        return "Institution", "Administrator"
    if len(parts) == 1:
        return parts[0][:150], "Administrator"
    return parts[0][:150], " ".join(parts[1:])[:150]


def _modules_for(application, plan):
    chosen = list(application.modules or [])
    if not chosen and plan is not None:
        chosen = list(plan.modules or [])
    if not chosen:
        chosen = list(DEFAULT_MODULES.get(application.institution_type, []))
    return [code for code in ALL_MODULES if code in set(chosen)]


def _consent_log(institution, application, actor, request):
    record = DataConsentLog.objects.create(
        institution=institution,
        application=application,
        kind=DataConsentLog.Kind.DATA_PROCESSING,
        version=DATA_PROCESSING_VERSION,
        statement_text=DATA_PROCESSING_STATEMENT,
        accepted_by_name=application.contact_person[:200],
        accepted_by_email=application.email,
        accepted_by=actor if getattr(actor, "is_authenticated", False) else None,
        ip_address=client_ip(request),
    )
    # The institution accepted the statement when it applied, so its settings
    # page should show the version and date from the first day.
    institution.data_processing_accepted_at = record.accepted_at
    institution.data_processing_version = record.version
    institution.save(
        update_fields=["data_processing_accepted_at", "data_processing_version"]
    )
    return record


def _send_welcome(institution, admin_user, application, plan, request=None):
    """The approval flow uses the same welcome email as direct onboarding."""
    from .onboarding import _current_invitation_link, send_welcome_email

    return send_welcome_email(
        institution,
        admin_user,
        request=request,
        invitation_link=_current_invitation_link(admin_user),
        application=application,
        plan=plan,
    )


def approve_application(application, actor, plan=None, request=None):
    """Approve once, create the institution, first admin and trial.

    The row is locked and its status is checked inside the transaction, so two
    reviewers pressing Approve at the same moment cannot create two institutions.
    """
    if application.status == InstitutionApplication.Status.APPROVED:
        raise ValidationError("This application has already been approved.")
    if not application.is_open:
        raise ValidationError("This application is closed.")

    with transaction.atomic():
        locked = (
            InstitutionApplication.objects.select_for_update()
            .select_related("preferred_plan")
            .get(pk=application.pk)
        )
        if locked.status == InstitutionApplication.Status.APPROVED:
            raise ValidationError("This application has already been approved.")
        chosen_plan = plan or locked.preferred_plan or Plan.objects.filter(is_active=True).first()
        if chosen_plan is None:
            raise ValidationError("Create a plan before approving an application.")

        institution = Institution.objects.create(
            name=locked.institution_name,
            slug=_unique_slug(locked.institution_name),
            type=locked.institution_type,
            address=locked.town,
            contact_email=locked.email,
            contact_phone=locked.phone,
            website=locked.website,
            is_active=False,
        )
        selected = _modules_for(locked, chosen_plan)
        with use_institution(institution):
            InstitutionModule.objects.bulk_create(
                [
                    InstitutionModule(institution=institution, code=code, enabled=code in selected)
                    for code in ALL_MODULES
                ]
            )
            from core.terminology import save_terminology

            save_terminology(institution, {})
            roles = ensure_roles(institution)
            from academics.models import SchoolSettings

            SchoolSettings.get(institution)

            from core.models import NumberFormat
            from core.numbering import DEFAULTS

            NumberFormat.objects.bulk_create(
                [
                    NumberFormat(institution=institution, kind=kind, **values)
                    for kind, values in DEFAULTS.items()
                ]
            )

            first_name, last_name = _split_name(locked.contact_person)
            admin_user = User(
                username=_unique_username(locked.email, first_name),
                email=locked.email,
                first_name=first_name,
                last_name=last_name,
                phone=locked.phone,
                role=User.Role.ADMIN,
                is_staff=True,
                is_active=True,
                institution=institution,
                primary_role=roles.get("institution_admin"),
            )
            admin_user.set_unusable_password()
            admin_user.save()

            from accounts.user_admin import create_invitation

            create_invitation(admin_user, actor=actor, request=request)

        now = timezone.now()
        cycle = Subscription.Cycle.MONTHLY
        trial_days = chosen_plan.trial_days or 0
        subscription = Subscription.objects.create(
            institution=institution,
            plan=chosen_plan,
            status=Subscription.Status.TRIALING if trial_days else Subscription.Status.ACTIVE,
            billing_cycle=cycle,
            trial_ends_at=now + timedelta(days=trial_days) if trial_days else None,
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )

        locked.status = InstitutionApplication.Status.APPROVED
        locked.decided_by = actor
        locked.decided_at = now
        locked.created_institution = institution
        locked.save(
            update_fields=[
                "status", "decided_by", "decided_at", "created_institution", "updated",
            ]
        )
        _event(
            locked, "approved", actor,
            f"Institution created on the {chosen_plan.name} plan.",
            {"institution_id": institution.pk, "plan": chosen_plan.code},
        )
        _consent_log(institution, locked, actor, request)
        _send_welcome(institution, admin_user, locked, chosen_plan, request=request)
        # Imported here because onboarding.py already imports this module.
        from .onboarding import SUPPORT_EMAIL, SUPPORT_PHONE

        _application_email(
            locked,
            f"Welcome to EduSphere ({locked.reference})",
            "emails/platform_application_approved.html",
            (
                f"Hello {locked.contact_person},\n\n"
                f"Your application for {locked.institution_name} has been approved.\n\n"
                "Check your inbox for the invitation to choose the administrator "
                "password, then follow the setup steps in the welcome email.\n\n"
                f"Reference: {locked.reference}\n"
                f"Need help? Call/WhatsApp {SUPPORT_PHONE} or email {SUPPORT_EMAIL}.\n"
            ),
            {
                "institution": institution,
                "user": admin_user,
                "plan": chosen_plan,
                "support_phone": SUPPORT_PHONE,
                "support_email": SUPPORT_EMAIL,
            },
            email_type=INSTITUTION_APPLICATION_APPROVED,
        )
        log_platform_action(
            actor,
            "application.approved",
            institution=institution,
            record_type="InstitutionApplication",
            record_id=locked.pk,
            detail={
                "reference": locked.reference,
                "plan": chosen_plan.code,
                "subscription_id": subscription.pk,
                "admin_user_id": admin_user.pk,
            },
            request=request,
        )
        log_action(
            actor,
            "institution.approved_from_application",
            institution=institution,
            after={"reference": locked.reference, "plan": chosen_plan.code},
            request=request,
        )
    # Keep the caller's copy in step with the locked row.
    application.refresh_from_db()
    return institution


# ---------- access requests ----------


def _institution_recipients(institution, include_high=False):
    users = []
    for user in User.objects.filter(institution=institution, is_active=True):
        if user.is_superuser or user.role == User.Role.ADMIN:
            users.append(user)
        elif user.has_code("admin.manage_settings") or user.has_code("admin.manage_users"):
            users.append(user)
    if include_high:
        for user in (institution.data_protection_officer, institution.principal):
            if user is not None and user.is_active and user not in users:
                users.append(user)
    return users


def _access_email(recipient, subject, body, institution, request=None, template="emails/platform_access_request.html", extra=None, email_type=PLATFORM_ACCESS_REQUEST):
    from core.notifications import absolute_url

    link = absolute_url(reverse("platform_admin:institution_access_requests"))
    context = {"institution": institution, "link": link, "body": body}
    context.update(extra or {})
    html_body = render_email_html(template, context)
    message = queue_message(
        "email", recipient, body, subject=subject, institution=institution,
        html_body=html_body, email_type=email_type,
    )
    send_message(message)
    return message


def _notify_access_request(access_request, request=None):
    institution = access_request.institution
    link = reverse("platform_admin:institution_access_requests")
    recipients = _institution_recipients(
        institution, include_high=access_request.needs_extra_approver
    )
    for user in recipients:
        notify(
            user,
            "EduSphere platform staff asked for access",
            (
                f"{access_request.requested_by.get_full_name() or access_request.requested_by.username} "
                f"asked for {', '.join(access_request.categories)} for "
                f"{access_request.duration_hours} hour(s)."
            ),
            link=link,
            category="access",
            institution=institution,
        )
        _access_email(
            user.email or user.username,
            "EduSphere is asking for access to your institution data",
            (
                f"Hello {user.get_full_name() or user.username},\n\n"
                f"{access_request.requested_by.get_full_name() or access_request.requested_by.username} "
                f"from EduSphere platform staff asked for consent to access:\n"
                f"{', '.join(access_request.categories)}\n"
                f"Mode: {access_request.get_mode_display()}\n"
                f"Reason: {access_request.reason}\n\n"
                f"Approve, narrow or deny it here:\n{link}\n\n"
                "Nothing is shared until you approve. If you do nothing, the "
                "request waits and then expires; it is never approved automatically.\n"
            ),
            institution,
            request,
            extra={"access_request": access_request, "link": link},
        )
    if access_request.needs_extra_approver and not (
        institution.data_protection_officer_id or institution.principal_id
    ):
        for user in recipients:
            notify(
                user,
                "Health or counselling access needs a DPO or principal",
                "Set a data protection officer or principal in institution settings before this request can be approved.",
                link=reverse("institution_settings"),
                category="access",
                institution=institution,
            )


def submit_access_request(
    actor,
    institution,
    categories,
    mode,
    reason,
    reference="",
    hours=DEFAULT_GRANT_HOURS,
    include_export=False,
    request=None,
    require_reauth=True,
):
    staff = require_platform_permission(actor, "platform.request_access")
    if require_reauth:
        blocked = ready_for_sensitive_action(staff, request)
        if blocked:
            raise ValidationError(blocked)
    reason = (reason or "").strip()
    if len(reason) < 20:
        raise ValidationError("Give a reason of at least 20 characters.")
    chosen = [code for code in categories if code in ACCESS_CATEGORY_CODES]
    if not chosen:
        raise ValidationError("Choose at least one category of data.")
    if "changes" in chosen and mode != AccessRequest.Mode.READ_WRITE:
        raise ValidationError("Making changes needs read-and-change access.")
    max_hours = min(
        int(getattr(institution, "access_grant_max_hours", DEFAULT_GRANT_HOURS)
            or DEFAULT_GRANT_HOURS),
        MAX_GRANT_HOURS,
    )
    try:
        hours = int(hours)
    except (TypeError, ValueError):
        hours = DEFAULT_GRANT_HOURS
    hours = max(1, min(hours, max_hours))
    access_request = AccessRequest.objects.create(
        institution=institution,
        requested_by=actor,
        categories=chosen,
        mode=mode if mode in AccessRequest.Mode.values else AccessRequest.Mode.READ,
        include_export=bool(include_export),
        reason=reason,
        reference=(reference or "")[:120],
        duration_hours=hours,
        expires_at=timezone.now() + timedelta(days=REQUEST_LIFETIME_DAYS),
    )
    access_request.full_clean()
    _notify_access_request(access_request, request)
    log_platform_action(
        actor,
        "access.requested",
        institution=institution,
        record_type="AccessRequest",
        record_id=access_request.pk,
        detail={
            "categories": chosen,
            "mode": access_request.mode,
            "hours": hours,
            "export": access_request.include_export,
        },
        request=request,
    )
    check_unusual_patterns(actor, request)
    return access_request


def submit_emergency_access(
    actor, institution, categories, justification, request=None
):
    staff = require_platform_permission(actor, "platform.emergency_access")
    blocked = ready_for_sensitive_action(staff, request)
    if blocked:
        raise ValidationError(blocked)
    justification = (justification or "").strip()
    if len(justification) < 20:
        raise ValidationError("Type the security incident or outage and why access is needed.")
    chosen = [code for code in categories if code in ACCESS_CATEGORY_CODES]
    if not chosen:
        raise ValidationError("Choose at least one category of data.")
    access_request = AccessRequest.objects.create(
        institution=institution,
        requested_by=actor,
        categories=chosen,
        mode=AccessRequest.Mode.READ,
        include_export=False,
        reason=justification,
        reference="Emergency break-glass",
        duration_hours=EMERGENCY_HOURS,
        is_emergency=True,
        emergency_justification=justification,
        flagged_for_review=True,
        expires_at=timezone.now() + timedelta(hours=EMERGENCY_HOURS),
    )
    _notify_access_request(access_request, request)
    for other in PlatformStaff.objects.select_related("user").filter(
        is_active=True, user__is_active=True
    ).exclude(user=actor):
        notify(
            other.user,
            "Emergency access needs a second approval",
            f"{actor.get_full_name() or actor.username} asked for emergency access to {institution.name}.",
            link=reverse("platform_admin:access_request_detail", args=[access_request.pk]),
            category="access",
        )
    log_platform_action(
        actor,
        "access.emergency_requested",
        institution=institution,
        record_type="AccessRequest",
        record_id=access_request.pk,
        detail={"categories": chosen, "justification": justification},
        request=request,
    )
    return access_request


def is_institution_approver(actor, institution):
    if not getattr(actor, "is_authenticated", False):
        return False
    if platform_staff_for(actor) is not None:
        return False
    if getattr(actor, "institution_id", None) != institution.pk:
        return False
    if getattr(actor, "is_superuser", False) or getattr(actor, "role", "") == User.Role.ADMIN:
        return True
    return actor.has_code("admin.manage_settings") or actor.has_code("admin.manage_users")


def _extra_approver(actor, institution):
    if institution.data_protection_officer_id == actor.pk:
        return True
    if institution.principal_id == actor.pk:
        return True
    return False


def decide_access_request(
    access_request,
    actor,
    approve,
    *,
    categories=None,
    hours=None,
    mode=None,
    include_export=None,
    reason="",
    request=None,
):
    if access_request.status != AccessRequest.Status.PENDING:
        raise ValidationError("This request has already been decided.")
    if actor.pk == access_request.requested_by_id:
        raise ValidationError("You cannot decide your own access request.")
    institution = access_request.institution
    if not is_institution_approver(actor, institution):
        raise PermissionDenied("Only an administrator of this institution can decide.")

    approved_categories = list(categories or access_request.categories)
    unknown = set(approved_categories) - set(access_request.categories)
    if unknown:
        raise ValidationError("You cannot widen the categories that were requested.")
    if not approved_categories:
        raise ValidationError("Approve at least one category, or deny the request.")

    if set(approved_categories) & HIGH_PROTECTION_CATEGORIES:
        if not _extra_approver(actor, institution):
            raise ValidationError(
                "Health and counselling access needs the institution's data "
                "protection officer or principal to approve."
            )

    now = timezone.now()
    if not approve:
        reason = (reason or "").strip()
        if len(reason) < 10:
            raise ValidationError("Give a reason for denying the request.")
        access_request.status = AccessRequest.Status.DENIED
        access_request.decided_by = actor
        access_request.decided_at = now
        access_request.decision_reason = reason
        access_request.approved_categories = []
        access_request.save(
            update_fields=[
                "status", "decided_by", "decided_at", "decision_reason",
                "approved_categories", "updated",
            ]
        )
        log_platform_action(
            actor, "access.denied", institution=institution,
            record_type="AccessRequest", record_id=access_request.pk,
            detail={"reason": reason}, request=request,
        )
        _access_email(
            access_request.requested_by.email or access_request.requested_by.username,
            f"Access denied for {institution.name}",
            (
                f"Your request for {institution.name} was denied.\n\n"
                f"Reason: {reason}\n\n"
                "You may ask again with a narrower request if the need is still there.\n"
            ),
            institution,
            request,
            template="emails/platform_access_decision.html",
            extra={"access_request": access_request, "approved": False},
            email_type=PLATFORM_ACCESS_DECISION,
        )
        return access_request

    max_hours = min(
        int(getattr(institution, "access_grant_max_hours", DEFAULT_GRANT_HOURS)
            or DEFAULT_GRANT_HOURS),
        MAX_GRANT_HOURS,
    )
    try:
        hours = int(hours or access_request.duration_hours)
    except (TypeError, ValueError):
        hours = access_request.duration_hours
    hours = max(1, min(hours, max_hours, access_request.duration_hours))
    chosen_mode = mode or access_request.mode
    if chosen_mode not in AccessRequest.Mode.values:
        chosen_mode = access_request.mode
    if chosen_mode == AccessRequest.Mode.READ_WRITE and access_request.mode != AccessRequest.Mode.READ_WRITE:
        chosen_mode = access_request.mode
    if "changes" in approved_categories and chosen_mode != AccessRequest.Mode.READ_WRITE:
        raise ValidationError("Making changes needs read-and-change access.")
    may_export = bool(access_request.include_export)
    if include_export is not None:
        may_export = may_export and bool(include_export)

    access_request.status = AccessRequest.Status.APPROVED
    access_request.decided_by = actor
    access_request.decided_at = now
    access_request.decision_reason = (reason or "").strip()
    access_request.approved_categories = approved_categories
    access_request.approved_mode = chosen_mode
    access_request.approved_include_export = may_export
    access_request.approved_duration_hours = hours
    access_request.expires_at = now + timedelta(hours=hours)
    access_request.save(
        update_fields=[
            "status", "decided_by", "decided_at", "decision_reason",
            "approved_categories", "approved_mode", "approved_include_export",
            "approved_duration_hours", "expires_at", "updated",
        ]
    )
    grant = DataAccessGrant.objects.create(
        access_request=access_request,
        institution=institution,
        platform_admin=access_request.requested_by,
        categories=approved_categories,
        mode=chosen_mode,
        include_export=may_export,
        starts_at=now,
        expires_at=now + timedelta(hours=hours),
        ip_address=client_ip(request),
    )
    log_platform_action(
        actor,
        "access.approved",
        institution=institution,
        grant=grant,
        record_type="AccessRequest",
        record_id=access_request.pk,
        detail={
            "categories": approved_categories,
            "mode": chosen_mode,
            "hours": hours,
            "export": may_export,
            "narrowed": approved_categories != access_request.categories
            or hours < access_request.duration_hours,
        },
        request=request,
    )
    _access_email(
        access_request.requested_by.email or access_request.requested_by.username,
        f"Access granted for {institution.name}",
        (
            f"Your request for {institution.name} was approved.\n\n"
            f"Categories: {', '.join(approved_categories)}\n"
            f"Mode: {grant.mode_label}\n"
            f"Until: {timezone.localtime(grant.expires_at):%Y-%m-%d %H:%M}\n"
            f"Grant id: {grant.pk}\n\n"
            "The institution may revoke this at any time.\n"
        ),
        institution,
        request,
        template="emails/platform_access_decision.html",
        extra={"access_request": access_request, "grant": grant, "approved": True},
        email_type=PLATFORM_ACCESS_DECISION,
    )
    return access_request


def approve_emergency(access_request, actor, request=None):
    staff = require_platform_permission(actor, "platform.approve_emergency")
    if not access_request.is_emergency:
        raise ValidationError("This is not an emergency request.")
    if access_request.status != AccessRequest.Status.PENDING:
        raise ValidationError("This request has already been decided.")
    if actor.pk == access_request.requested_by_id:
        raise ValidationError("A second platform administrator must approve emergency access.")
    if not staff.two_factor_enabled or not staff.two_factor_secret:
        raise ValidationError("Switch on two-factor authentication first.")

    now = timezone.now()
    access_request.status = AccessRequest.Status.APPROVED
    access_request.decided_by = actor
    access_request.decided_at = now
    access_request.second_approver = actor
    access_request.second_approved_at = now
    access_request.approved_categories = list(access_request.categories)
    access_request.approved_mode = AccessRequest.Mode.READ
    access_request.approved_include_export = False
    access_request.approved_duration_hours = EMERGENCY_HOURS
    access_request.expires_at = now + timedelta(hours=EMERGENCY_HOURS)
    access_request.save(
        update_fields=[
            "status", "decided_by", "decided_at", "second_approver",
            "second_approved_at", "approved_categories", "approved_mode",
            "approved_include_export", "approved_duration_hours", "expires_at",
            "updated",
        ]
    )
    grant = DataAccessGrant.objects.create(
        access_request=access_request,
        institution=access_request.institution,
        platform_admin=access_request.requested_by,
        categories=access_request.approved_categories,
        mode=AccessRequest.Mode.READ,
        include_export=False,
        starts_at=now,
        expires_at=now + timedelta(hours=EMERGENCY_HOURS),
        ip_address=client_ip(request),
    )
    institution = access_request.institution
    for user in _institution_recipients(institution, include_high=True):
        notify(
            user,
            "Emergency EduSphere access was used",
            (
                f"{access_request.requested_by.get_full_name() or access_request.requested_by.username} "
                f"was given emergency read-only access to {institution.name} for two hours."
            ),
            link=reverse("platform_admin:institution_data_access"),
            category="access",
            institution=institution,
        )
    log_platform_action(
        actor,
        "access.emergency_approved",
        institution=institution,
        grant=grant,
        record_type="AccessRequest",
        record_id=access_request.pk,
        detail={"second_approver": actor.pk, "hours": EMERGENCY_HOURS},
        request=request,
    )
    return access_request


def revoke_grant(grant, actor, reason="", request=None):
    if not is_institution_approver(actor, grant.institution):
        raise PermissionDenied("Only an administrator of this institution can revoke access.")
    if grant.revoked_at is not None:
        raise ValidationError("This grant has already been revoked.")
    grant.revoked_at = timezone.now()
    grant.revoked_by = actor
    grant.revoked_reason = (reason or "").strip()
    grant.save(update_fields=["revoked_at", "revoked_by", "revoked_reason"])
    access_request = grant.access_request
    access_request.status = AccessRequest.Status.REVOKED
    access_request.decision_reason = (reason or "").strip()
    access_request.save(update_fields=["status", "decision_reason", "updated"])
    log_platform_action(
        actor,
        "access.revoked",
        institution=grant.institution,
        grant=grant,
        record_type="DataAccessGrant",
        record_id=grant.pk,
        detail={"reason": reason},
        request=request,
    )
    notify(
        grant.platform_admin,
        f"Access to {grant.institution.name} was revoked",
        "The institution withdrew its consent. Stop using the grant immediately.",
        category="access",
        institution=grant.institution,
    )
    return grant


def expire_stale_access(now=None):
    """Expire pending requests and grants whose time is up."""
    now = now or timezone.now()
    expired_requests = AccessRequest.objects.filter(
        status=AccessRequest.Status.PENDING, expires_at__lte=now
    )
    count = 0
    for access_request in expired_requests:
        access_request.status = AccessRequest.Status.EXPIRED
        access_request.save(update_fields=["status", "updated"])
        log_platform_action(
            None,
            "access.expired",
            institution=access_request.institution,
            record_type="AccessRequest",
            record_id=access_request.pk,
            detail={"reason": "The institution did not respond before the request expired."},
        )
        count += 1
    grants = DataAccessGrant.objects.filter(
        revoked_at__isnull=True, expired_at__isnull=True, expires_at__lte=now
    ).select_related("access_request")
    for grant in grants:
        # Mark the grant itself so it stops working even when a scheduled sweep
        # is run ahead of the wall clock that is_active uses.
        grant.expired_at = now
        grant.save(update_fields=["expired_at"])
        if grant.access_request.status == AccessRequest.Status.APPROVED:
            grant.access_request.status = AccessRequest.Status.EXPIRED
            grant.access_request.save(update_fields=["status", "updated"])
            log_platform_action(
                None, "access.grant_expired", institution=grant.institution,
                grant=grant, record_type="DataAccessGrant", record_id=grant.pk,
            )
            count += 1
    return count


def check_unusual_patterns(actor, request=None):
    """Alert other platform admins about requests, denials or bulk views."""
    now = timezone.now()
    window = now - timedelta(hours=24)
    reasons = []
    request_count = AccessRequest.objects.filter(
        requested_by=actor, created__gte=window
    ).count()
    if request_count > 5:
        reasons.append(f"{request_count} access requests in 24 hours")
    denials = AccessRequest.objects.filter(
        requested_by=actor, status=AccessRequest.Status.DENIED, decided_at__gte=window
    ).count()
    if denials >= 3:
        reasons.append(f"{denials} denied requests in 24 hours")
    views = PlatformAuditLog.objects.filter(
        actor=actor, action__startswith="platform.", created__gte=now - timedelta(hours=1)
    ).count()
    if views > 100:
        reasons.append(f"{views} protected views in one hour")
    if not reasons:
        return []
    others = [
        staff.user
        for staff in PlatformStaff.objects.select_related("user").filter(
            is_active=True, user__is_active=True
        ).exclude(user=actor)
    ]
    for user in others:
        notify(
            user,
            "Unusual platform access pattern",
            f"{actor.get_full_name() or actor.username}: " + "; ".join(reasons),
            link=reverse("platform_admin:audit_log"),
            category="security",
        )
    log_platform_action(
        actor,
        "platform.pattern_alert",
        detail={"reasons": reasons},
        request=request,
    )
    return reasons
