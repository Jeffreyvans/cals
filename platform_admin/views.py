"""Views for public applications, platform administration and consent.

Public pages use the brown marketing theme. Platform screens use the ordinary
administration shell. Institution consent screens are only for an institution
administrator, never for platform staff.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.audit import log_action
from core.models import AuditEntry, Institution, InstitutionModule, Notification
from core.modules import ALL_MODULES, MODULE_LABELS
from core.notifications import notify

from . import onboarding, services
from .access import institution_admin_required, platform_required
from .forms import (
    AccessDecisionForm,
    AccessRequestForm,
    AnnouncementForm,
    DataProcessingAcceptanceForm,
    EmergencyAccessForm,
    InstitutionAdministratorForm,
    InstitutionApplicationForm,
    InstitutionCreateForm,
    InstitutionEditForm,
    PasswordConfirmForm,
    PlanForm,
    PlatformInvoiceForm,
    PlatformPaymentForm,
    PlatformRoleForm,
    PlatformStaffForm,
    SubscriptionForm,
    TwoFactorForm,
)
from .legal import (
    DATA_PROCESSING_SHORT,
    DATA_PROCESSING_STATEMENT,
    DATA_PROCESSING_VERSION,
    PLATFORM_PRIVACY_NOTICE,
)
from .models import (
    ACCESS_CATEGORIES,
    AccessRequest,
    ApplicationDocument,
    DataAccessGrant,
    DataConsentLog,
    InstitutionApplication,
    Plan,
    PlatformAnnouncement,
    PlatformAuditLog,
    PlatformInvoice,
    PlatformPayment,
    PlatformRequest,
    PlatformRole,
    PlatformSetting,
    PlatformStaff,
    Subscription,
)
from .permissions import PLATFORM_PERMISSION_LOOKUP, PLATFORM_PERMISSIONS
from .twofa import (
    mark_reauth,
    new_secret,
    provisioning_uri,
)
from .twofa import (
    verify as verify_totp,
)

User = get_user_model()


# ---------- small helpers ----------


def _platform_url(name, *args):
    return reverse(f"platform_admin:{name}", args=args)


def _csv_response(filename, header, rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _institution_or_404(pk):
    institution = Institution.objects.filter(pk=pk).first()
    if institution is None or institution.deleted_at is not None:
        raise Http404("No such institution.")
    return institution


def _may_approve_high(institution, user):
    return user.pk in {
        institution.data_protection_officer_id,
        institution.principal_id,
    }


def _next_or_default(request, fallback):
    value = request.POST.get("next") or request.GET.get("next") or ""
    if value.startswith("/") and not value.startswith("//"):
        return value
    return fallback


# =====================================================================
# Public application
# =====================================================================


def apply(request):
    """The public "Apply to use EduSphere" form."""
    form = InstitutionApplicationForm(request.POST or None, request.FILES or None)
    if request.method == "POST":
        # A real person never fills the hidden field. Pretend the application
        # arrived so a bot learns nothing, and never create a row.
        if (request.POST.get("website_url") or "").strip():
            services.record_application_attempt(request)
            return render(request, "platform_admin/apply_thank_you.html", status=200)
        if services.application_rate_limited(request):
            form.add_error(
                None,
                "Too many applications were sent from this connection. "
                "Please try again later.",
            )
        elif form.is_valid():
            services.record_application_attempt(request)
            application = services.create_application(
                form.cleaned_data, request.FILES, request
            )
            return redirect(
                "platform_admin:apply_status", token=application.applicant_token
            )
        else:
            services.record_application_attempt(request)
    return render(
        request,
        "platform_admin/apply.html",
        {
            "form": form,
            "statement": DATA_PROCESSING_STATEMENT,
            "statement_version": DATA_PROCESSING_VERSION,
            "statement_short": DATA_PROCESSING_SHORT,
        },
    )


def apply_status(request, token):
    application = (
        InstitutionApplication.objects.select_related("preferred_plan", "created_institution")
        .filter(applicant_token=token)
        .first()
    )
    if application is None:
        raise Http404("No application matches that link.")
    events = application.events.all()
    conversation = application.messages.all()
    documents = application.documents.all()
    return render(
        request,
        "platform_admin/apply_status.html",
        {
            "application": application,
            "events": events,
            "conversation": conversation,
            "documents": documents,
            "can_reply": application.can_reply,
        },
    )


@require_POST
def apply_reply(request, token):
    application = get_object_or_404(InstitutionApplication, applicant_token=token)
    try:
        services.applicant_reply(application, request.POST.get("message", ""), request)
        messages.success(request, "Your reply was sent to EduSphere.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("platform_admin:apply_status", token=token)


@require_POST
def apply_withdraw(request, token):
    application = get_object_or_404(InstitutionApplication, applicant_token=token)
    try:
        services.withdraw_application(application, request)
        messages.success(request, "The application was withdrawn.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("platform_admin:apply_status", token=token)


def platform_privacy(request):
    return render(
        request,
        "platform_admin/privacy.html",
        {"notice": PLATFORM_PRIVACY_NOTICE, "statement": DATA_PROCESSING_STATEMENT},
    )


def data_processing_statement(request):
    return render(
        request,
        "platform_admin/data_processing.html",
        {"statement": DATA_PROCESSING_STATEMENT, "version": DATA_PROCESSING_VERSION},
    )


# =====================================================================
# Platform dashboard and inbox
# =====================================================================


@platform_required("platform.view_institutions")
def dashboard(request):
    staff = request.platform_staff
    now = timezone.now()
    open_applications = InstitutionApplication.objects.filter(
        status__in=[
            InstitutionApplication.Status.SUBMITTED,
            InstitutionApplication.Status.UNDER_REVIEW,
            InstitutionApplication.Status.MORE_INFORMATION_NEEDED,
        ]
    ).count()
    institutions = Institution.objects.filter(deleted_at__isnull=True)
    active_grants = DataAccessGrant.objects.filter(
        revoked_at__isnull=True, expires_at__gt=now
    ).select_related("institution", "platform_admin")
    return render(
        request,
        "platform_admin/dashboard.html",
        {
            "staff": staff,
            "institution_count": institutions.count(),
            "active_institution_count": institutions.filter(is_active=True).count(),
            "suspended_count": institutions.filter(suspended_at__isnull=False).count(),
            "application_count": open_applications,
            "pending_access_count": AccessRequest.objects.filter(
                status=AccessRequest.Status.PENDING
            ).count(),
            "open_request_count": PlatformRequest.objects.filter(
                status=PlatformRequest.Status.OPEN
            ).count(),
            "active_grants": active_grants,
            "recent_applications": InstitutionApplication.objects.all()[:6],
            "recent_audit": PlatformAuditLog.objects.all()[:8],
            "chain_ok": PlatformAuditLog.verify_chain(),
            "unread_notifications": Notification.objects.filter(
                user=request.user, read_at__isnull=True
            ).count(),
        },
    )


@platform_required("platform.view_inbox")
def inbox(request):
    status = request.GET.get("status", "open")
    applications = InstitutionApplication.objects.filter(
        status__in=[
            InstitutionApplication.Status.SUBMITTED,
            InstitutionApplication.Status.UNDER_REVIEW,
            InstitutionApplication.Status.MORE_INFORMATION_NEEDED,
        ]
    )
    if status != "open":
        applications = InstitutionApplication.objects.all()
    platform_requests = PlatformRequest.objects.all()
    if status == "open":
        platform_requests = platform_requests.filter(status=PlatformRequest.Status.OPEN)
    access_requests = AccessRequest.objects.select_related("institution", "requested_by")
    if status == "open":
        access_requests = access_requests.filter(status=AccessRequest.Status.PENDING)
    rows = []
    for application in applications.select_related("assigned_to"):
        rows.append(
            {
                "kind": "Institution application",
                "title": application.institution_name,
                "reference": application.reference,
                "institution": application.institution_name,
                "status": application.get_status_display(),
                "created": application.created,
                "url": _platform_url("application_detail", application.pk),
            }
        )
    for item in platform_requests.select_related("institution", "requested_by"):
        rows.append(
            {
                "kind": item.get_kind_display(),
                "title": item.title,
                "reference": item.reference or f"#{item.pk}",
                "institution": item.institution.name if item.institution else "Platform",
                "status": item.get_status_display(),
                "created": item.created,
                "url": _platform_url("platform_request_detail", item.pk),
            }
        )
    for access_request in access_requests:
        rows.append(
            {
                "kind": "Access request",
                "title": f"{access_request.requested_by} asked for institution data",
                "reference": f"AR-{access_request.pk}",
                "institution": access_request.institution.name,
                "status": access_request.get_status_display(),
                "created": access_request.created,
                "url": _platform_url("access_request_detail", access_request.pk),
            }
        )
    rows.sort(key=lambda row: row["created"], reverse=True)
    counts = {
        "applications": InstitutionApplication.objects.filter(
            status__in=[
                InstitutionApplication.Status.SUBMITTED,
                InstitutionApplication.Status.UNDER_REVIEW,
                InstitutionApplication.Status.MORE_INFORMATION_NEEDED,
            ]
        ).count(),
        "requests": PlatformRequest.objects.filter(
            status=PlatformRequest.Status.OPEN
        ).count(),
        "access": AccessRequest.objects.filter(
            status=AccessRequest.Status.PENDING
        ).count(),
    }
    return render(
        request,
        "platform_admin/inbox.html",
        {"rows": rows, "status": status, "counts": counts, "staff": request.platform_staff},
    )


# =====================================================================
# Applications
# =====================================================================


@platform_required("platform.view_applications")
def applications(request):
    queryset = InstitutionApplication.objects.select_related(
        "preferred_plan", "assigned_to", "created_institution"
    )
    status = request.GET.get("status", "")
    search = (request.GET.get("q") or "").strip()
    plan = request.GET.get("plan", "")
    if status:
        queryset = queryset.filter(status=status)
    if plan:
        queryset = queryset.filter(preferred_plan_id=plan)
    if search:
        queryset = queryset.filter(
            Q(institution_name__icontains=search)
            | Q(reference__icontains=search)
            | Q(email__icontains=search)
            | Q(contact_person__icontains=search)
            | Q(town__icontains=search)
        )
    return render(
        request,
        "platform_admin/applications.html",
        {
            "applications": queryset,
            "statuses": InstitutionApplication.Status.choices,
            "plans": Plan.objects.all(),
            "status": status,
            "search": search,
            "plan": plan,
            "staff": request.platform_staff,
        },
    )


@platform_required("platform.view_applications")
def application_detail(request, pk):
    application = get_object_or_404(
        InstitutionApplication.objects.select_related(
            "preferred_plan", "assigned_to", "created_institution", "decided_by"
        ),
        pk=pk,
    )
    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if not request.platform_staff.has_permission("platform.manage_applications"):
                raise PermissionDenied("Your platform role cannot decide applications.")
            if action == "assign":
                reviewer = User.objects.filter(pk=request.POST.get("reviewer")).first()
                if reviewer is None:
                    raise ValidationError("Choose a reviewer.")
                services.assign_application(application, reviewer, request.user, request)
                messages.success(request, "Application assigned.")
            elif action == "note":
                services.add_internal_note(
                    application, request.user, request.POST.get("note", ""), request
                )
                messages.success(request, "Note saved.")
            elif action == "more_information":
                services.request_more_information(
                    application, request.user, request.POST.get("message", ""), request
                )
                messages.success(request, "The applicant was asked for more information.")
            elif action == "reject":
                services.reject_application(
                    application, request.user, request.POST.get("reason", ""), request
                )
                messages.success(request, "Application rejected and the applicant emailed.")
            elif action == "approve":
                plan = None
                if request.POST.get("plan"):
                    plan = get_object_or_404(Plan, pk=request.POST["plan"])
                institution = services.approve_application(
                    application, request.user, plan=plan, request=request
                )
                messages.success(
                    request,
                    f"{institution.name} was created. The administrator was emailed a "
                    "single-use password link.",
                )
                return redirect("platform_admin:application_detail", pk=application.pk)
            else:
                raise ValidationError("Unknown action.")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        return redirect("platform_admin:application_detail", pk=application.pk)
    return render(
        request,
        "platform_admin/application_detail.html",
        {
            "application": application,
            "documents": application.documents.all(),
            "events": application.events.select_related("actor"),
            "messages_list": application.messages.select_related("author"),
            "reviewers": User.objects.filter(
                platform_staff__is_active=True, platform_staff__isnull=False
            ).distinct(),
            "plans": Plan.objects.filter(is_active=True),
            "staff": request.platform_staff,
            "can_manage": request.platform_staff.has_permission("platform.manage_applications"),
            "statement": DATA_PROCESSING_STATEMENT,
        },
    )


@platform_required("platform.view_applications")
def application_document(request, pk, document_pk):
    application = get_object_or_404(InstitutionApplication, pk=pk)
    document = get_object_or_404(ApplicationDocument, pk=document_pk, application=application)
    services.log_platform_action(
        request.user,
        "application.document_viewed",
        record_type="ApplicationDocument",
        record_id=document.pk,
        detail={"application": application.reference},
        request=request,
    )
    try:
        handle = document.file.open("rb")
    except OSError:
        raise Http404("The document is missing from storage.") from None
    response = HttpResponse(
        handle.read(), content_type=document.content_type or "application/octet-stream"
    )
    response["Content-Disposition"] = f'inline; filename="{document.original_name or "document"}"'
    return response


# =====================================================================
# Platform requests
# =====================================================================


@platform_required("platform.view_inbox")
def platform_request_detail(request, pk):
    item = get_object_or_404(
        PlatformRequest.objects.select_related("institution", "requested_by", "assigned_to"),
        pk=pk,
    )
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action in {"approve", "reject", "close"}:
            item.status = {
                "approve": PlatformRequest.Status.APPROVED,
                "reject": PlatformRequest.Status.REJECTED,
                "close": PlatformRequest.Status.CLOSED,
            }[action]
            item.decision_reason = request.POST.get("reason", "")[:2000]
            item.decided_at = timezone.now()
            item.save(update_fields=["status", "decision_reason", "decided_at"])
            services.log_platform_action(
                request.user,
                f"platform_request.{action}",
                institution=item.institution,
                record_type="PlatformRequest",
                record_id=item.pk,
                detail={"kind": item.kind, "reason": item.decision_reason},
                request=request,
            )
            messages.success(request, "The request was updated.")
            return redirect("platform_admin:platform_request_detail", pk=item.pk)
    return render(
        request,
        "platform_admin/platform_request_detail.html",
        {"item": item, "staff": request.platform_staff},
    )


# =====================================================================
# Institutions
# =====================================================================


@platform_required("platform.view_institutions")
def institutions(request):
    queryset = Institution.objects.filter(deleted_at__isnull=True).annotate(
        user_count=Count("users", distinct=True),
    )
    status = request.GET.get("status", "")
    search = (request.GET.get("q") or "").strip()
    if status == "active":
        queryset = queryset.filter(is_active=True, suspended_at__isnull=True)
    elif status == "suspended":
        queryset = queryset.filter(suspended_at__isnull=False)
    elif status == "archived":
        queryset = queryset.filter(archived_at__isnull=False)
    elif status == "inactive":
        queryset = queryset.filter(is_active=False)
    if search:
        queryset = queryset.filter(
            Q(name__icontains=search) | Q(slug__icontains=search) | Q(address__icontains=search)
        )
    from django.core.paginator import Paginator

    page = Paginator(queryset.order_by("name"), 25).get_page(request.GET.get("page"))
    return render(
        request,
        "platform_admin/institutions.html",
        {
            "institutions": page.object_list,
            "page_obj": page,
            "status": status,
            "search": search,
            "staff": request.platform_staff,
        },
    )


@platform_required("platform.manage_institutions")
def institution_create(request):
    """Create an institution and switch on the modules its type expects."""
    from django.db import transaction

    from core.modules import apply_defaults

    form = InstitutionCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            institution = form.save()
            apply_defaults(institution)
            services.log_platform_action(
                request.user,
                "institution.created",
                institution=institution,
                record_type="Institution",
                record_id=institution.pk,
                after={
                    "name": institution.name,
                    "slug": institution.slug,
                    "type": institution.type,
                },
                request=request,
            )
        messages.success(
            request,
            f"{institution.name} was created. Add its first administrator from the "
            "institution's Admins screen.",
        )
        return redirect("platform_admin:institution_detail", pk=institution.pk)
    return render(
        request,
        "platform_admin/institution_create.html",
        {"form": form, "staff": request.platform_staff},
    )


@platform_required("platform.view_institutions")
def institution_detail(request, pk):
    institution = _institution_or_404(pk)
    form = InstitutionEditForm(instance=institution)
    if request.method == "POST":
        action = request.POST.get("action", "edit")
        if action == "edit":
            if not request.platform_staff.has_permission("platform.manage_institutions"):
                raise PermissionDenied("Your platform role cannot edit institutions.")
            # Capture the stored values before the bound form applies its
            # cleaned data to the instance during validation.
            before = {
                "name": institution.name,
                "type": institution.type,
                "access_grant_max_hours": institution.access_grant_max_hours,
            }
            form = InstitutionEditForm(request.POST, instance=institution)
            if form.is_valid():
                form.save()
                services.log_platform_action(
                    request.user,
                    "institution.edited",
                    institution=institution,
                    record_type="Institution",
                    record_id=institution.pk,
                    before=before,
                    after={
                        "name": institution.name,
                        "type": institution.type,
                        "access_grant_max_hours": institution.access_grant_max_hours,
                    },
                    request=request,
                )
                messages.success(request, "Institution details saved.")
                return redirect("platform_admin:institution_detail", pk=institution.pk)
        elif action == "resend_welcome":
            if not request.platform_staff.has_permission("platform.manage_admins"):
                raise PermissionDenied("Your platform role cannot manage administrators.")
            admin_user = get_object_or_404(
                onboarding.administrators(institution), pk=request.POST.get("user")
            )
            message = onboarding.resend_welcome_email(
                institution, admin_user, actor=request.user, request=request
            )
            if message is None or getattr(message, "status", "") != "sent":
                messages.error(request, "We could not send the email. Try again in a few minutes.")
            else:
                messages.success(request, "Welcome email sent again.")
            return redirect("platform_admin:institution_detail", pk=institution.pk)
        elif action in {"suspend", "reinstate", "archive", "restore", "delete"}:
            if not request.platform_staff.has_permission("platform.manage_institutions"):
                raise PermissionDenied("Your platform role cannot change institutions.")
            reason = (request.POST.get("reason") or "").strip()
            from .lifecycle import change_lifecycle

            try:
                before, after = change_lifecycle(institution, action, reason=reason)
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
                return redirect("platform_admin:institution_detail", pk=institution.pk)
            services.log_platform_action(
                request.user,
                f"institution.{action}",
                institution=institution,
                record_type="Institution",
                record_id=institution.pk,
                before=before,
                after={**after, "reason": reason},
                request=request,
            )
            messages.success(request, f"Institution {action}d.")
            if action == "delete":
                return redirect("platform_admin:institutions")
            return redirect("platform_admin:institution_detail", pk=institution.pk)
    subscription = (
        Subscription.objects.select_related("plan")
        .filter(institution=institution)
        .order_by("-created")
        .first()
    )
    return render(
        request,
        "platform_admin/institution_detail.html",
        {
            "institution": institution,
            "form": form,
            "subscription": subscription,
            "invoice_count": institution.platform_invoices.count(),
            "user_count": User.objects.filter(institution=institution).count(),
            "staff": request.platform_staff,
            "statement": DATA_PROCESSING_STATEMENT,
            "consent_logs": institution.data_consent_logs.all()[:5],
            "checklist": onboarding.admin_checklist(institution),
            "welcome_admins": onboarding.administrators(institution).exclude(email=""),
        },
    )


@platform_required("platform.view_institutions")
def institution_modules(request, pk):
    institution = _institution_or_404(pk)
    if request.method == "POST":
        if not request.platform_staff.has_permission("platform.manage_modules"):
            raise PermissionDenied("Your platform role cannot change modules.")
        code = request.POST.get("code", "")
        if code not in ALL_MODULES:
            raise Http404("Unknown module.")
        enabled = request.POST.get("enabled") == "1"
        row, _created = InstitutionModule.objects.get_or_create(
            institution=institution, code=code, defaults={"enabled": enabled}
        )
        before = {"enabled": row.enabled}
        row.enabled = enabled
        row.save(update_fields=["enabled"])
        services.log_platform_action(
            request.user,
            "institution.module_changed",
            institution=institution,
            record_type="InstitutionModule",
            record_id=row.pk,
            before=before,
            after={"code": code, "enabled": enabled},
            request=request,
        )
        messages.success(request, f"{MODULE_LABELS.get(code, code)} updated.")
        return redirect("platform_admin:institution_modules", pk=institution.pk)
    switches = {row.code: row.enabled for row in institution.modules.all()}
    return render(
        request,
        "platform_admin/institution_modules.html",
        {
            "institution": institution,
            "modules": [
                {
                    "code": code,
                    "label": MODULE_LABELS.get(code, code),
                    "enabled": switches.get(code, False),
                }
                for code in ALL_MODULES
            ],
            "staff": request.platform_staff,
        },
    )


@platform_required("platform.manage_admins")
def institution_admins(request, pk):
    """List and manage the administrators of one institution.

    Platform staff may manage access here. They never see student, health,
    counselling, finance or results records from this screen.
    """
    institution = _institution_or_404(pk)
    admins = onboarding.administrators(institution)
    if request.method == "POST":
        action = request.POST.get("action", "")
        admin_user = get_object_or_404(admins, pk=request.POST.get("user"))
        try:
            secret = onboarding.institution_admin_action(
                institution, admin_user, action,
                actor=request.user, request=request,
            )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect("platform_admin:institution_admins", pk=institution.pk)
        if secret:
            request.session["onboarding_credential"] = {
                "user_id": admin_user.pk,
                "institution_id": institution.pk,
                "secret": secret,
                "access_mode": "temporary_password" if action == "reset_password" else "invitation",
                "action": action,
            }
            request.session.modified = True
            return redirect(
                "platform_admin:onboarding_credential",
                pk=institution.pk, user_pk=admin_user.pk,
            )
        messages.success(request, "Administrator updated.")
        return redirect("platform_admin:institution_admins", pk=institution.pk)
    return render(
        request,
        "platform_admin/institution_admins.html",
        {
            "institution": institution,
            "admins": admins,
            "staff": request.platform_staff,
            "checklist": onboarding.admin_checklist(institution),
        },
    )


@platform_required("platform.manage_admins")
def onboarding_admin_create(request, pk):
    """Add the first (or another) institution administrator."""
    institution = _institution_or_404(pk)
    form = InstitutionAdministratorForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            result = onboarding.add_administrator(
                institution,
                full_name=form.cleaned_data["full_name"],
                email=form.cleaned_data["email"],
                phone=form.cleaned_data["phone"],
                access_mode=form.cleaned_data["access_mode"],
                actor=request.user,
                request=request,
            )
        except ValidationError as exc:
            form.add_error(None, " ".join(exc.messages))
        else:
            request.session["onboarding_credential"] = {
                "user_id": result.user.pk,
                "institution_id": institution.pk,
                "secret": result.secret,
                "access_mode": result.access_mode,
                "email_failed": result.email_failed,
                "action": "create",
            }
            request.session.modified = True
            return redirect(
                "platform_admin:onboarding_credential",
                pk=institution.pk, user_pk=result.user.pk,
            )
    return render(
        request,
        "platform_admin/onboarding_admin_form.html",
        {"institution": institution, "form": form, "staff": request.platform_staff},
    )


@platform_required("platform.manage_admins")
def onboarding_credential(request, pk, user_pk):
    """Show a temporary password or one-time link exactly once."""
    institution = _institution_or_404(pk)
    admin_user = get_object_or_404(onboarding.administrators(institution), pk=user_pk)
    data = request.session.pop("onboarding_credential", None)
    if not data or data.get("user_id") != admin_user.pk or data.get("institution_id") != institution.pk:
        messages.info(request, "Those one-time details have already been shown.")
        return redirect("platform_admin:institution_detail", pk=institution.pk)
    return render(
        request,
        "platform_admin/onboarding_credential.html",
        {
            "institution": institution,
            "admin_user": admin_user,
            "secret": data.get("secret", ""),
            "access_mode": data.get("access_mode", ""),
            "email_failed": bool(data.get("email_failed")),
            "action": data.get("action", ""),
            "staff": request.platform_staff,
        },
    )


# =====================================================================
# Plans, subscriptions, invoices and payments
# =====================================================================


@platform_required("platform.manage_plans")
def plans(request):
    form = PlanForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        plan = form.save()
        services.log_platform_action(
            request.user, "plan.created", record_type="Plan", record_id=plan.pk,
            after={"code": plan.code, "name": plan.name}, request=request,
        )
        messages.success(request, "Plan created.")
        return redirect("platform_admin:plans")
    return render(
        request,
        "platform_admin/plans.html",
        {"plans": Plan.objects.all(), "form": form, "staff": request.platform_staff},
    )


@platform_required("platform.manage_plans")
def plan_detail(request, pk):
    plan = get_object_or_404(Plan, pk=pk)
    form = PlanForm(request.POST or None, instance=plan)
    # Read the stored values before validation hands the new ones to the model.
    before = {"name": plan.name, "monthly_price": str(plan.monthly_price)}
    if request.method == "POST" and form.is_valid():
        form.save()
        services.log_platform_action(
            request.user, "plan.edited", record_type="Plan", record_id=plan.pk,
            before=before,
            after={"name": plan.name, "monthly_price": str(plan.monthly_price)},
            request=request,
        )
        messages.success(request, "Plan saved.")
        return redirect("platform_admin:plan_detail", pk=plan.pk)
    return render(
        request,
        "platform_admin/plan_detail.html",
        {"plan": plan, "form": form, "staff": request.platform_staff},
    )


@platform_required("platform.manage_plans")
def subscriptions(request):
    form = SubscriptionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        subscription = form.save()
        services.log_platform_action(
            request.user, "subscription.created", institution=subscription.institution,
            record_type="Subscription", record_id=subscription.pk,
            after={"plan": subscription.plan.code, "status": subscription.status},
            request=request,
        )
        messages.success(request, "Subscription saved.")
        return redirect("platform_admin:subscriptions")
    return render(
        request,
        "platform_admin/subscriptions.html",
        {
            "subscriptions": Subscription.objects.select_related("institution", "plan"),
            "form": form,
            "staff": request.platform_staff,
        },
    )


@platform_required("platform.manage_billing")
def invoices(request):
    form = PlatformInvoiceForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        invoice = form.save()
        services.log_platform_action(
            request.user, "invoice.created", institution=invoice.institution,
            record_type="PlatformInvoice", record_id=invoice.pk,
            after={"number": invoice.number, "amount": str(invoice.amount)},
            request=request,
        )
        messages.success(request, "Invoice created.")
        return redirect("platform_admin:invoice_detail", pk=invoice.pk)
    return render(
        request,
        "platform_admin/invoices.html",
        {
            "invoices": PlatformInvoice.objects.select_related("institution"),
            "form": form,
            "staff": request.platform_staff,
        },
    )


@platform_required("platform.manage_billing")
def invoice_detail(request, pk):
    invoice = get_object_or_404(
        PlatformInvoice.objects.select_related("institution", "subscription"), pk=pk
    )
    form = PlatformPaymentForm(request.POST or None, initial={"invoice": invoice})
    if request.method == "POST" and form.is_valid():
        payment = form.save(commit=False)
        payment.invoice = invoice
        payment.recorded_by = request.user
        payment.save()
        if invoice.balance <= Decimal("0") and invoice.status != PlatformInvoice.Status.PAID:
            invoice.status = PlatformInvoice.Status.PAID
            invoice.paid_on = payment.paid_at.date()
            invoice.save(update_fields=["status", "paid_on"])
        services.log_platform_action(
            request.user, "payment.recorded", institution=invoice.institution,
            record_type="PlatformPayment", record_id=payment.pk,
            after={"invoice": invoice.number, "amount": str(payment.amount)},
            request=request,
        )
        messages.success(request, "Payment recorded.")
        return redirect("platform_admin:invoice_detail", pk=invoice.pk)
    return render(
        request,
        "platform_admin/invoice_detail.html",
        {
            "invoice": invoice,
            "payments": invoice.payments.all(),
            "form": form,
            "staff": request.platform_staff,
        },
    )


@platform_required("platform.manage_billing")
def payments(request):
    return render(
        request,
        "platform_admin/payments.html",
        {
            "payments": PlatformPayment.objects.select_related("invoice", "invoice__institution"),
            "staff": request.platform_staff,
        },
    )


# =====================================================================
# Platform staff and roles
# =====================================================================


@platform_required("platform.manage_staff", main_admin=True)
def staff_list(request):
    form = PlatformStaffForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            institution = Institution.objects.order_by("pk").first()
            if institution is None:
                messages.error(request, "Create an institution before adding platform staff.")
                return redirect("platform_admin:staff")
            user = User.objects.create_user(
                username=email.split("@")[0][:40] or "platform",
                email=email,
                first_name=form.cleaned_data["first_name"],
                last_name=form.cleaned_data.get("last_name", ""),
                institution=institution,
                role=User.Role.STAFF,
                is_staff=False,
            )
            user.set_unusable_password()
            user.save(update_fields=["password"])
        staff, _created = PlatformStaff.objects.update_or_create(
            user=user,
            defaults={
                "role": form.cleaned_data["role"],
                "is_main_admin": form.cleaned_data.get("is_main_admin", False),
                "is_active": form.cleaned_data.get("is_active", True),
            },
        )
        services.log_platform_action(
            request.user, "platform_staff.saved", record_type="PlatformStaff",
            record_id=staff.pk,
            after={"user": user.pk, "role": staff.role.code if staff.role else ""},
            request=request,
        )
        messages.success(request, "Platform staff member saved.")
        return redirect("platform_admin:staff")
    return render(
        request,
        "platform_admin/staff.html",
        {
            "staff_rows": PlatformStaff.objects.select_related("user", "role"),
            "form": form,
            "staff": request.platform_staff,
        },
    )


@platform_required("platform.manage_staff", main_admin=True)
def staff_detail(request, pk):
    row = get_object_or_404(PlatformStaff.objects.select_related("user", "role"), pk=pk)
    if request.method == "POST":
        if row.user_id == request.user.pk and request.POST.get("action") == "deactivate":
            messages.error(request, "You cannot deactivate your own platform account.")
        elif request.POST.get("action") == "deactivate":
            row.is_active = False
            row.save(update_fields=["is_active"])
            services.log_platform_action(
                request.user, "platform_staff.deactivated", record_type="PlatformStaff",
                record_id=row.pk, request=request,
            )
        elif request.POST.get("action") == "activate":
            row.is_active = True
            row.save(update_fields=["is_active"])
            services.log_platform_action(
                request.user, "platform_staff.activated", record_type="PlatformStaff",
                record_id=row.pk, request=request,
            )
        elif request.POST.get("action") == "role":
            role = get_object_or_404(PlatformRole, pk=request.POST.get("role"))
            row.role = role
            row.is_main_admin = role.code == "main_admin"
            row.save(update_fields=["role", "is_main_admin"])
            services.log_platform_action(
                request.user, "platform_staff.role_changed", record_type="PlatformStaff",
                record_id=row.pk, after={"role": role.code}, request=request,
            )
        messages.success(request, "Platform staff member updated.")
        return redirect("platform_admin:staff_detail", pk=row.pk)
    return render(
        request,
        "platform_admin/staff_detail.html",
        {"row": row, "roles": PlatformRole.objects.all(), "staff": request.platform_staff},
    )


@platform_required("platform.manage_staff", main_admin=True)
def roles(request):
    form = PlatformRoleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        role = form.save()
        services.log_platform_action(
            request.user, "platform_role.created", record_type="PlatformRole",
            record_id=role.pk, after={"code": role.code}, request=request,
        )
        messages.success(request, "Platform role created.")
        return redirect("platform_admin:roles")
    return render(
        request,
        "platform_admin/roles.html",
        {
            "roles": PlatformRole.objects.all(),
            "permissions": PLATFORM_PERMISSIONS,
            "form": form,
            "staff": request.platform_staff,
        },
    )


@platform_required("platform.manage_staff", main_admin=True)
def role_detail(request, pk):
    role = get_object_or_404(PlatformRole, pk=pk)
    if request.method == "POST":
        if role.is_system:
            messages.error(request, "Built-in roles keep their permissions.")
        else:
            role.permissions = [
                code for code in request.POST.getlist("permissions")
                if code in PLATFORM_PERMISSION_LOOKUP
            ]
            role.save(update_fields=["permissions"])
            services.log_platform_action(
                request.user, "platform_role.permissions_changed",
                record_type="PlatformRole", record_id=role.pk,
                after={"permissions": role.permissions}, request=request,
            )
            messages.success(request, "Role permissions saved.")
        return redirect("platform_admin:role_detail", pk=role.pk)
    return render(
        request,
        "platform_admin/role_detail.html",
        {
            "role": role,
            "permissions": PLATFORM_PERMISSIONS,
            "held": set(role.permissions or []),
            "staff": request.platform_staff,
        },
    )


# =====================================================================
# Operations
# =====================================================================


@platform_required("platform.view_health")
def health(request):
    from academics.models import Student

    return render(
        request,
        "platform_admin/health.html",
        {
            "institution_count": Institution.objects.filter(deleted_at__isnull=True).count(),
            "student_count": Student.all_objects.count(),
            "user_count": User.objects.count(),
            "failed_messages": Notification.objects.none().count(),
            "chain_ok": PlatformAuditLog.verify_chain(),
            "last_audit": PlatformAuditLog.objects.first(),
            "staff": request.platform_staff,
        },
    )


@platform_required("platform.view_health")
def usage(request):
    from academics.models import Student

    rows = []
    for institution in Institution.objects.filter(deleted_at__isnull=True):
        rows.append(
            {
                "institution": institution,
                "users": User.objects.filter(institution=institution).count(),
                "students": Student.all_objects.filter(institution=institution).count(),
                "storage": ApplicationDocument.objects.filter(
                    application__email__isnull=False
                ).none().count(),
                "last_activity": AuditEntry.objects.filter(
                    institution=institution
                ).values_list("created", flat=True).first(),
                "plan": (
                    Subscription.objects.filter(institution=institution)
                    .select_related("plan")
                    .first()
                ),
            }
        )
    return render(
        request,
        "platform_admin/usage.html",
        {"rows": rows, "staff": request.platform_staff},
    )


@platform_required("platform.view_health")
def errors(request):
    from core.models import OutboundMessage

    failed = OutboundMessage.objects.filter(
        status=OutboundMessage.Status.FAILED
    ).order_by("-created")[:100]
    denials = PlatformAuditLog.objects.filter(
        action__in=["platform.access_denied", "permission.denied"]
    )[:100]
    return render(
        request,
        "platform_admin/errors.html",
        {"failed": failed, "denials": denials, "staff": request.platform_staff},
    )


@platform_required("platform.view_health")
def email_health(request, pk=None):
    """Platform-wide email health: provider, log, failures and delivery events."""
    from django.conf import settings

    from core.email_types import EMAIL_TYPE_LABELS
    from core.models import EmailEvent, EmailSuppression, OutboundMessage
    from core.notifications import EMAIL_FAILURE_MESSAGE, send_message

    if request.method == "POST":
        message = OutboundMessage.all_objects.filter(pk=pk).first()
        if message is not None:
            message.status = OutboundMessage.Status.QUEUED
            message.retries = 0
            message.next_attempt_at = None
            message.sent_at = None
            message.provider_response = ""
            message.save(update_fields=[
                "status", "retries", "next_attempt_at", "sent_at", "provider_response",
            ])
            send_message(message, force_inline=True)
            if getattr(message, "status", "") == OutboundMessage.Status.SENT:
                messages.success(request, "The message was resent.")
            else:
                messages.error(request, EMAIL_FAILURE_MESSAGE)
        return redirect("platform_admin:email_health")

    recent = OutboundMessage.all_objects.select_related("institution").order_by("-created")[:50]
    failures = OutboundMessage.all_objects.filter(
        status=OutboundMessage.Status.FAILED
    ).count()
    by_type = [
        (EMAIL_TYPE_LABELS.get(row["email_type"], row["email_type"]), row["total"])
        for row in OutboundMessage.all_objects.values("email_type").annotate(
            total=Count("pk")).order_by("email_type")
    ]
    by_status = list(
        OutboundMessage.all_objects.values("status").annotate(
            total=Count("pk")).order_by("status")
    )
    return render(request, "platform_admin/email_health.html", {
        "recent": recent,
        "failures": failures,
        "provider": getattr(settings, "EMAIL_PROVIDER", "?"),
        "backend": getattr(settings, "EMAIL_BACKEND", ""),
        "from_email": getattr(settings, "DEFAULT_FROM_EMAIL", ""),
        "site_url": getattr(settings, "SITE_URL", ""),
        "by_type": by_type,
        "by_status": by_status,
        "delivered": EmailEvent.all_objects.filter(
            kind=EmailEvent.Kind.DELIVERED).count(),
        "bounced": EmailEvent.all_objects.filter(kind=EmailEvent.Kind.BOUNCED).count(),
        "blocked": EmailEvent.all_objects.filter(kind=EmailEvent.Kind.BLOCKED).count(),
        "spam": EmailEvent.all_objects.filter(kind=EmailEvent.Kind.SPAM).count(),
        "complained": EmailEvent.all_objects.filter(
            kind=EmailEvent.Kind.COMPLAINED).count(),
        "suppressed": EmailSuppression.objects.count(),
        "staff": request.platform_staff,
    })


@platform_required("platform.manage_backups")
def backups(request):
    setting = PlatformSetting.get("backups", {}) or {}
    if request.method == "POST":
        services.log_platform_action(
            request.user, "backup.requested", detail={"note": request.POST.get("note", "")[:500]},
            request=request,
        )
        messages.success(request, "A backup run was recorded in the audit log.")
        return redirect("platform_admin:backups")
    return render(
        request,
        "platform_admin/backups.html",
        {"setting": setting, "staff": request.platform_staff},
    )


@platform_required("platform.view_audit")
def audit_log(request):
    queryset = PlatformAuditLog.objects.select_related("actor", "institution", "grant")
    action = request.GET.get("action", "")
    institution_id = request.GET.get("institution", "")
    search = (request.GET.get("q") or "").strip()
    if action:
        queryset = queryset.filter(action__startswith=action)
    if institution_id:
        queryset = queryset.filter(institution_id=institution_id)
    if search:
        queryset = queryset.filter(
            Q(record_id__icontains=search) | Q(detail__icontains=search)
        )
    return render(
        request,
        "platform_admin/audit.html",
        {
            "entries": queryset[:500],
            "chain_ok": PlatformAuditLog.verify_chain(),
            "institutions": Institution.objects.all(),
            "action": action,
            "institution_id": institution_id,
            "search": search,
            "staff": request.platform_staff,
        },
    )


@platform_required("platform.view_audit")
def audit_export(request):
    rows = PlatformAuditLog.objects.select_related("actor", "institution", "grant")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["time", "actor", "action", "institution", "record_type", "record_id",
         "grant", "ip", "detail", "prev_hash", "entry_hash"]
    )
    for entry in rows.iterator():
        writer.writerow(
            [
                timezone.localtime(entry.created).isoformat(),
                entry.actor.get_username() if entry.actor else "",
                entry.action,
                entry.institution.name if entry.institution else "",
                entry.record_type,
                entry.record_id,
                entry.grant_id or "",
                entry.ip_address or "",
                entry.detail,
                entry.prev_hash,
                entry.entry_hash,
            ]
        )
    response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="platform-audit.csv"'
    return response


@platform_required("platform.send_announcements")
def announcements(request):
    form = AnnouncementForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        announcement = form.save(commit=False)
        announcement.created_by = request.user
        announcement.save()
        services.log_platform_action(
            request.user, "announcement.published", record_type="PlatformAnnouncement",
            record_id=announcement.pk, after={"title": announcement.title}, request=request,
        )
        if announcement.audience in {
            PlatformAnnouncement.Audience.EVERYONE,
            PlatformAnnouncement.Audience.INSTITUTIONS,
        }:
            for institution in Institution.objects.filter(is_active=True, deleted_at__isnull=True):
                for admin in User.objects.filter(
                    institution=institution, role=User.Role.ADMIN, is_active=True
                ):
                    notify(
                        admin,
                        announcement.title,
                        announcement.body[:500],
                        link=_platform_url("dashboard"),
                        category="announcement",
                        institution=institution,
                    )
        messages.success(request, "Announcement published.")
        return redirect("platform_admin:announcements")
    return render(
        request,
        "platform_admin/announcements.html",
        {
            "announcements": PlatformAnnouncement.objects.all(),
            "form": form,
            "staff": request.platform_staff,
        },
    )


@platform_required("platform.manage_settings", main_admin=True)
def settings_view(request):
    keys = ["email", "branding", "legal", "support"]
    if request.method == "POST":
        for key in keys:
            PlatformSetting.set(key, {"value": request.POST.get(key, "")}, actor=request.user)
        services.log_platform_action(
            request.user, "platform_settings.saved", record_type="PlatformSetting",
            detail={"keys": keys}, request=request,
        )
        messages.success(request, "Platform settings saved.")
        return redirect("platform_admin:settings")
    current = {key: (PlatformSetting.get(key, {}) or {}).get("value", "") for key in keys}
    return render(
        request,
        "platform_admin/settings.html",
        {
            "current": current,
            "statement": DATA_PROCESSING_STATEMENT,
            "version": DATA_PROCESSING_VERSION,
            "privacy": PLATFORM_PRIVACY_NOTICE,
            "staff": request.platform_staff,
        },
    )


# =====================================================================
# Access requests (platform side)
# =====================================================================


@platform_required("platform.request_access")
def access_requests(request):
    queryset = AccessRequest.objects.select_related(
        "institution", "requested_by", "decided_by"
    )
    status = request.GET.get("status", "")
    if status:
        queryset = queryset.filter(status=status)
    return render(
        request,
        "platform_admin/access_requests.html",
        {
            "access_requests": queryset[:300],
            "statuses": AccessRequest.Status.choices,
            "status": status,
            "staff": request.platform_staff,
        },
    )


@platform_required("platform.request_access")
def access_request_create(request):
    form = AccessRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            access_request = services.submit_access_request(
                request.user,
                form.cleaned_data["institution"],
                form.cleaned_data["categories"],
                form.cleaned_data["mode"],
                form.cleaned_data["reason"],
                reference=form.cleaned_data.get("reference", ""),
                hours=form.cleaned_data.get("duration_hours") or 24,
                include_export=form.cleaned_data.get("include_export", False),
                request=request,
            )
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
        else:
            messages.success(
                request,
                "The institution administrators were notified. Nothing is shared "
                "until they approve.",
            )
            return redirect("platform_admin:access_request_detail", pk=access_request.pk)
    return render(
        request,
        "platform_admin/access_request_create.html",
        {"form": form, "staff": request.platform_staff},
    )


@platform_required("platform.request_access")
def access_request_detail(request, pk):
    access_request = get_object_or_404(
        AccessRequest.objects.select_related(
            "institution", "requested_by", "decided_by", "second_approver"
        ),
        pk=pk,
    )
    grant = DataAccessGrant.objects.filter(access_request=access_request).first()
    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action == "cancel" and access_request.is_pending:
                access_request.status = AccessRequest.Status.CANCELLED
                access_request.save(update_fields=["status", "updated"])
                services.log_platform_action(
                    request.user, "access.cancelled", institution=access_request.institution,
                    record_type="AccessRequest", record_id=access_request.pk, request=request,
                )
                messages.success(request, "The request was cancelled.")
            elif action == "emergency_approve":
                services.approve_emergency(access_request, request.user, request)
                messages.success(request, "Emergency access approved for two hours.")
            elif action == "flag":
                access_request.flagged_for_review = True
                access_request.save(update_fields=["flagged_for_review", "updated"])
                messages.success(request, "The request was flagged for review.")
            else:
                raise ValidationError("Unknown action.")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        return redirect("platform_admin:access_request_detail", pk=access_request.pk)
    return render(
        request,
        "platform_admin/access_request_detail.html",
        {
            "access_request": access_request,
            "grant": grant,
            "staff": request.platform_staff,
            "can_second": (
                access_request.is_emergency
                and access_request.is_pending
                and request.user.pk != access_request.requested_by_id
                and request.platform_staff.has_permission("platform.approve_emergency")
            ),
        },
    )


@platform_required("platform.emergency_access")
def emergency_access_create(request):
    form = EmergencyAccessForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            access_request = services.submit_emergency_access(
                request.user,
                form.cleaned_data["institution"],
                form.cleaned_data["categories"],
                form.cleaned_data["justification"],
                request=request,
            )
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
        else:
            messages.warning(
                request,
                "Emergency request recorded. A second platform administrator must "
                "approve it before any access begins.",
            )
            return redirect("platform_admin:access_request_detail", pk=access_request.pk)
    return render(
        request,
        "platform_admin/emergency_create.html",
        {"form": form, "staff": request.platform_staff},
    )


@platform_required("platform.request_access")
def grants(request):
    now = timezone.now()
    queryset = DataAccessGrant.objects.select_related(
        "institution", "platform_admin", "access_request"
    )
    status = request.GET.get("status", "")
    if status == "active":
        queryset = queryset.filter(
            revoked_at__isnull=True, expired_at__isnull=True, expires_at__gt=now
        )
    elif status == "revoked":
        queryset = queryset.filter(revoked_at__isnull=False)
    elif status == "expired":
        queryset = queryset.filter(
            Q(revoked_at__isnull=True, expires_at__lte=now)
            | Q(expired_at__isnull=False)
        )
    return render(
        request,
        "platform_admin/grants.html",
        {"grants": queryset[:300], "status": status, "staff": request.platform_staff},
    )


# =====================================================================
# Two-factor and re-authentication
# =====================================================================


@platform_required()
def security(request):
    staff = request.platform_staff
    form = TwoFactorForm(request.POST or None)
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "start":
            staff.two_factor_secret = new_secret()
            staff.save(update_fields=["two_factor_secret"])
            messages.info(request, "Scan the code, then enter one code to switch 2FA on.")
            return redirect("platform_admin:security")
        if action == "enable" and form.is_valid():
            if not staff.two_factor_secret:
                messages.error(request, "Start the setup first.")
            elif verify_totp(staff.two_factor_secret, form.cleaned_data["code"]):
                staff.two_factor_enabled = True
                staff.save(update_fields=["two_factor_enabled"])
                mark_reauth(request, staff)
                services.log_platform_action(
                    request.user, "platform_staff.two_factor_enabled",
                    record_type="PlatformStaff", record_id=staff.pk, request=request,
                )
                messages.success(request, "Two-factor authentication is on.")
            else:
                form.add_error("code", "That code did not match. Try the next one.")
        if action == "disable":
            staff.two_factor_enabled = False
            staff.save(update_fields=["two_factor_enabled"])
            messages.warning(request, "Two-factor authentication is off.")
            return redirect("platform_admin:security")
    uri = (
        provisioning_uri(staff.two_factor_secret, request.user.email)
        if staff.two_factor_secret and not staff.two_factor_enabled
        else ""
    )
    return render(
        request,
        "platform_admin/security.html",
        {
            "form": form,
            "staff": staff,
            "uri": uri,
            "secret": staff.two_factor_secret if not staff.two_factor_enabled else "",
        },
    )


@platform_required()
def reauth(request):
    form = PasswordConfirmForm(request.POST or None)
    next_url = _next_or_default(request, _platform_url("dashboard"))
    if request.method == "POST" and form.is_valid():
        if request.user.check_password(form.cleaned_data["password"]):
            mark_reauth(request, request.platform_staff)
            services.log_platform_action(
                request.user, "platform_staff.reauthenticated",
                record_type="PlatformStaff", record_id=request.platform_staff.pk,
                request=request,
            )
            messages.success(request, "Password confirmed for the next 15 minutes.")
            return redirect(next_url)
        form.add_error("password", "That password is not correct.")
    return render(
        request,
        "platform_admin/reauth.html",
        {"form": form, "next": next_url, "staff": request.platform_staff},
    )


# =====================================================================
# Institution consent screens
# =====================================================================


@institution_admin_required
def institution_access_requests(request):
    institution = request.institution
    queryset = AccessRequest.objects.filter(institution=institution).select_related(
        "requested_by", "decided_by"
    )
    status = request.GET.get("status", "")
    if status:
        queryset = queryset.filter(status=status)
    return render(
        request,
        "platform_admin/consent_requests.html",
        {
            "access_requests": queryset[:200],
            "statuses": AccessRequest.Status.choices,
            "status": status,
            "institution": institution,
            "pending_count": AccessRequest.objects.filter(
                institution=institution, status=AccessRequest.Status.PENDING
            ).count(),
        },
    )


@institution_admin_required
def institution_access_request_detail(request, pk):
    access_request = get_object_or_404(
        AccessRequest.objects.select_related(
            "institution", "requested_by", "decided_by"
        ),
        pk=pk,
        institution=request.institution,
    )
    grant = DataAccessGrant.objects.filter(access_request=access_request).first()
    form = AccessDecisionForm(
        initial={
            "categories": access_request.categories,
            "mode": access_request.mode,
            "hours": access_request.duration_hours,
            "include_export": access_request.include_export,
        }
    )
    return render(
        request,
        "platform_admin/consent_request_detail.html",
        {
            "access_request": access_request,
            "grant": grant,
            "form": form,
            "institution": request.institution,
            "category_help": {code: help_text for code, _label, help_text in ACCESS_CATEGORIES},
            "may_approve_high": _may_approve_high(request.institution, request.user),
            "is_pending": access_request.is_pending,
        },
    )


@require_POST
@institution_admin_required
def institution_access_decide(request, pk):
    access_request = get_object_or_404(
        AccessRequest, pk=pk, institution=request.institution
    )
    form = AccessDecisionForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Check the categories, time and reason.")
        return redirect("platform_admin:institution_access_request_detail", pk=pk)
    approve = request.POST.get("decision") == "approve"
    try:
        services.decide_access_request(
            access_request,
            request.user,
            approve,
            categories=form.cleaned_data.get("categories") or access_request.categories,
            hours=form.cleaned_data.get("hours"),
            mode=form.cleaned_data.get("mode") or None,
            include_export=form.cleaned_data.get("include_export"),
            reason=form.cleaned_data.get("reason", ""),
            request=request,
        )
    except (ValidationError, PermissionDenied) as exc:
        detail = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
        messages.error(request, detail)
    else:
        messages.success(
            request,
            "Access approved for the time you chose."
            if approve
            else "Access denied. EduSphere staff were told the reason.",
        )
    return redirect("platform_admin:institution_access_request_detail", pk=pk)


@require_POST
@institution_admin_required
def institution_grant_revoke(request, pk):
    grant = get_object_or_404(
        DataAccessGrant, pk=pk, institution=request.institution
    )
    try:
        services.revoke_grant(
            grant, request.user, request.POST.get("reason", ""), request
        )
        messages.success(request, "Access was revoked immediately.")
    except (ValidationError, PermissionDenied) as exc:
        detail = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
        messages.error(request, detail)
    return redirect("platform_admin:institution_data_access")


@institution_admin_required
def institution_data_access(request):
    institution = request.institution
    queryset = PlatformAuditLog.objects.filter(institution=institution).select_related(
        "actor", "grant"
    )
    action = request.GET.get("action", "")
    grant_id = request.GET.get("grant", "")
    if action:
        queryset = queryset.filter(action__startswith=action)
    if grant_id:
        queryset = queryset.filter(grant_id=grant_id)
    entries = queryset[:1000]
    return render(
        request,
        "platform_admin/data_access.html",
        {
            "institution": institution,
            "entries": entries,
            "action": action,
            "grant_id": grant_id,
            "active_grants": DataAccessGrant.objects.filter(
                institution=institution,
                revoked_at__isnull=True,
                expired_at__isnull=True,
                expires_at__gt=timezone.now(),
            ).select_related("platform_admin"),
            "grants": DataAccessGrant.objects.filter(institution=institution).select_related(
                "platform_admin"
            )[:100],
        },
    )


@institution_admin_required
def institution_data_access_export(request):
    institution = request.institution
    rows = PlatformAuditLog.objects.filter(institution=institution).select_related(
        "actor", "grant"
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["time", "who", "action", "record", "record_id", "grant", "ip", "detail"]
    )
    for entry in rows.iterator():
        writer.writerow(
            [
                timezone.localtime(entry.created).isoformat(),
                entry.actor.get_username() if entry.actor else "system",
                entry.action,
                entry.record_type,
                entry.record_id,
                entry.grant_id or "",
                entry.ip_address or "",
                entry.detail,
            ]
        )
    response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="who-accessed-our-data.csv"'
    return response


@institution_admin_required
def institution_consent_log(request):
    institution = request.institution
    logs = DataConsentLog.objects.filter(institution=institution)
    if request.GET.get("export") == "csv":
        return _csv_response(
            "data-consent-log.csv",
            ["kind", "version", "accepted_at", "accepted_by_name", "accepted_by_email", "hash"],
            [
                [
                    log.get_kind_display(),
                    log.version,
                    timezone.localtime(log.accepted_at).isoformat(),
                    log.accepted_by_name,
                    log.accepted_by_email,
                    log.statement_hash,
                ]
                for log in logs
            ],
        )
    return render(
        request,
        "platform_admin/consent_log.html",
        {
            "institution": institution,
            "logs": logs,
            "statement": DATA_PROCESSING_STATEMENT,
            "version": DATA_PROCESSING_VERSION,
        },
    )


@require_POST
@institution_admin_required
def institution_accept_data_processing(request):
    institution = request.institution
    form = DataProcessingAcceptanceForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Confirm the statement and type your name.")
        return redirect("platform_admin:institution_consent_log")
    log = DataConsentLog.objects.create(
        institution=institution,
        kind=DataConsentLog.Kind.DATA_PROCESSING,
        version=DATA_PROCESSING_VERSION,
        statement_text=DATA_PROCESSING_STATEMENT,
        accepted_by_name=form.cleaned_data["confirmed_by"][:200],
        accepted_by_email=request.user.email,
        accepted_by=request.user,
        ip_address=services.client_ip(request),
    )
    institution.data_processing_accepted_at = log.accepted_at
    institution.data_processing_version = log.version
    institution.save(update_fields=["data_processing_accepted_at", "data_processing_version"])
    log_action(
        request.user, "institution.data_processing_accepted", institution=institution,
        after={"version": log.version, "hash": log.statement_hash}, request=request,
    )
    messages.success(request, "The data processing statement was recorded.")
    return redirect("platform_admin:institution_consent_log")
