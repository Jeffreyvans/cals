"""Web screens for groups, sponsors, domains and resellers.

Aggregate pages never query a student row by name. Student-level pages call
``require_student_grant`` first, which reuses the platform's consent grant.
"""

import io
import json
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import login, logout
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts.forms import IdentifierAuthenticationForm
from core.models import Institution
from core.tenancy import use_institution
from enterprise.access import (
    ALL_GROUP_ROLES,
    SPONSOR_WRITE_ROLES,
    GroupRole,
    active_sponsor_accounts_for,
    feature_enabled,
    feature_required,
    group_required,
    log_group_action,
    log_sponsor_action,
    require_sponsor_sensitive_action,
    require_student_grant,
    reseller_accounts_for,
    reseller_required,
    sponsor_required,
    student_level_grant,
)
from enterprise.documents import build_sponsor_receipt_pdf
from enterprise.forms import ResellerAccountForm, SponsorAccountForm, SponsorLinkForm
from enterprise.models import (
    GroupBilling,
    GroupMembership,
    Organisation,
    ResellerAccount,
    ResellerCommission,
    SponsorAccount,
)
from enterprise.services import (
    allocate_sponsor_payment,
    assign_group_role,
    attach_institution,
    create_consolidated_invoice,
    create_organisation,
    create_reseller_account,
    create_sponsor_account,
    create_sponsor_link,
    detach_institution,
    donor_report,
    effective_setting,
    issue_sponsor_credit_note,
    mark_commission_paid,
    refresh_group_alerts,
    request_group_student_access,
    reseller_portal_data,
    resend_sponsor_verification,
    revoke_group_role,
    save_campus_override,
    save_shared_setting,
    set_sharing_level,
    setting_preview,
    sponsor_commitments,
    sponsor_receipts,
    sponsor_statement,
    verify_sponsor_account,
    withdraw_sponsor_sharing,
)
from enterprise.services import (
    group_dashboard as group_figures,
)
from enterprise.settings_registry import SETTING_KEYS, SHARED_SETTINGS
from finance.models import Payment
from platform_admin.access import platform_required
from platform_admin.forms import PasswordConfirmForm, TwoFactorForm
from platform_admin.twofa import mark_reauth, new_secret, provisioning_uri
from platform_admin.twofa import verify as verify_totp

# ---------- platform administration ----------


@platform_required("platform.manage_institutions")
def organisation_list(request):
    organisations = Organisation.objects.all()
    return render(
        request, "enterprise/organisation_list.html", {"organisations": organisations}
    )


@platform_required("platform.manage_institutions")
def organisation_create(request):
    if request.method == "POST":
        try:
            org = create_organisation(
                name=request.POST.get("name", "").strip(),
                slug=request.POST.get("slug", "").strip(),
                org_type=request.POST.get("type", Organisation.Type.TRUST),
                actor=request.user,
                contact_email=request.POST.get("contact_email", ""),
                contact_phone=request.POST.get("contact_phone", ""),
                address=request.POST.get("address", ""),
                website=request.POST.get("website", ""),
                primary_color=request.POST.get("primary_color", ""),
                accent_color=request.POST.get("accent_color", ""),
            )
        except ValidationError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Organisation {org.name} created.")
            return redirect("enterprise:organisation_detail", pk=org.pk)
    return render(
        request, "enterprise/organisation_form.html",
        {"types": Organisation.Type.choices},
    )


@platform_required("platform.manage_institutions")
def organisation_detail(request, pk):
    org = get_object_or_404(Organisation, pk=pk)
    if request.method == "POST":
        try:
            action = request.POST.get("action", "")
            if action == "attach":
                institution = get_object_or_404(
                    Institution, pk=request.POST.get("institution")
                )
                attach_institution(
                    org, institution, actor=request.user,
                    campus_label=request.POST.get("campus_label", ""),
                    sort_order=request.POST.get("sort_order", 0),
                )
            elif action == "detach":
                institution = get_object_or_404(
                    Institution, pk=request.POST.get("institution")
                )
                detach_institution(org, institution, actor=request.user)
            elif action == "role":
                from django.contrib.auth import get_user_model

                user = get_object_or_404(
                    get_user_model(), pk=request.POST.get("user")
                )
                assign_group_role(org, user, request.POST.get("role"), actor=request.user)
            elif action == "revoke":
                from django.contrib.auth import get_user_model

                user = get_object_or_404(
                    get_user_model(), pk=request.POST.get("user")
                )
                revoke_group_role(org, user, actor=request.user)
            elif action == "billing":
                billing, _ = GroupBilling.objects.get_or_create(
                    organisation=org, defaults={"mode": GroupBilling.Mode.SEPARATE}
                )
                mode = request.POST.get("mode", GroupBilling.Mode.SEPARATE)
                if mode not in GroupBilling.Mode.values:
                    raise ValidationError("Choose a recognised billing mode.")
                billing.mode = mode
                billing.save(update_fields=["mode", "updated_at"])
            elif action == "discount":
                billing, _ = GroupBilling.objects.get_or_create(
                    organisation=org, defaults={}
                )
                try:
                    rate = Decimal(request.POST.get("discount_rate", "0"))
                except InvalidOperation as exc:
                    raise ValidationError("Enter a valid discount percentage.") from exc
                if not Decimal("0") <= rate <= Decimal("100"):
                    raise ValidationError("The discount must be between 0 and 100 percent.")
                billing.discount_rate = rate
                billing.save(update_fields=["discount_rate", "updated_at"])
            log_group_action(
                request, f"group.platform.{action}", org,
                detail={"campus_ids": [i.pk for i in org.campuses()]},
            )
        except (ValidationError, ValueError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Organisation updated.")
            return redirect("enterprise:organisation_detail", pk=org.pk)
    memberships = org.memberships.select_related("institution")
    users = org.group_users.select_related("user")
    attached_ids = set(memberships.values_list("institution_id", flat=True))
    available = Institution.objects.filter(is_active=True).exclude(pk__in=attached_ids)
    return render(
        request, "enterprise/organisation_detail.html",
        {
            "organisation": org, "memberships": memberships, "users": users,
            "available": available, "roles": GroupMembership.Role.choices,
            "billing": GroupBilling.objects.filter(organisation=org).first(),
            "modes": GroupBilling.Mode.choices,
        },
    )


# ---------- group sign-in and SSO ----------


def group_login(request, slug):
    """Branded group sign-in at /g/<slug>/ with a campus chooser."""
    org = get_object_or_404(Organisation, slug=slug, status=Organisation.Status.ACTIVE)
    if not feature_enabled_for_organisation(org):
        raise Http404("Groups are not enabled for this organisation.")
    campuses = org.campuses()
    form = IdentifierAuthenticationForm(request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.get_user()
        membership = GroupMembership.objects.filter(
            user=user, organisation=org, is_active=True
        ).first()
        campus_id = request.POST.get("campus")
        campus = campuses.filter(pk=campus_id).first() if campus_id else None
        if membership is None or campus is None:
            logout(request)
            form.add_error(
                None, "Use a group account and choose one of this group's campuses."
            )
        else:
            login(request, user)
            request.session["group_campus_id"] = campus.pk
            request.session["group_slug"] = org.slug
            next_url = request.POST.get("next") or request.GET.get("next")
            if next_url and url_has_allowed_host_and_scheme(
                next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
            ):
                return redirect(next_url)
            return redirect("enterprise:group_dashboard", slug=org.slug)
    return render(
        request, "enterprise/group_login.html",
        {
            "organisation": org, "campuses": campuses, "form": form,
            "next_url": request.POST.get("next") or request.GET.get("next"),
        },
    )


def feature_enabled_for_organisation(org, code="groups"):
    """Any attached campus with the feature enabled allows organisation sign-in."""
    return any(feature_enabled(code, campus) for campus in org.campuses())


@group_required(*ALL_GROUP_ROLES)
@feature_required("groups")
def group_dashboard(request, slug):
    organisation = request.organisation
    dashboard = group_dashboard_data(organisation, **_dashboard_filters(request))
    alerts = refresh_group_alerts(organisation, dashboard)
    return render(
        request, "enterprise/group_dashboard.html",
        {
            "organisation": organisation, "dashboard": dashboard, "alerts": alerts,
            "membership": request.group_membership,
            "school_types": Institution.Type.choices,
            "campus": _selected_campus(request, organisation),
        },
    )


def group_dashboard_data(organisation, campus_ids=None, term_id=None, school_type=None,
                         start=None, end=None):
    return group_figures(organisation, campus_ids, term_id, school_type, start, end)


def _dashboard_filters(request):
    """Parse dashboard filters once so the page and its exports agree."""
    filters = request.GET
    campus_ids = [value for value in filters.getlist("campus") if value.isdigit()]
    term = filters.get("term") or ""
    term_id = term if term.isdigit() else None
    school_type = filters.get("school_type") or None
    try:
        start = date.fromisoformat(filters["start"]) if filters.get("start") else None
        end = date.fromisoformat(filters["end"]) if filters.get("end") else None
    except ValueError:
        start = end = None
    return {
        "campus_ids": campus_ids, "term_id": term_id,
        "school_type": school_type, "start": start, "end": end,
    }


def _selected_campus(request, organisation):
    campus_id = request.session.get("group_campus_id")
    campus = organisation.campuses().filter(pk=campus_id).first()
    if campus is None:
        campus = organisation.campuses().first()
    return campus


@require_POST
@group_required(*ALL_GROUP_ROLES)
@feature_required("groups")
def group_switch_campus(request, slug):
    """SSO inside a group: switch campus without another password prompt."""
    campus = request.organisation.campuses().filter(
        pk=request.POST.get("campus")
    ).first()
    if campus is None:
        messages.error(request, "Choose a campus in this group.")
    else:
        request.session["group_campus_id"] = campus.pk
        messages.success(request, f"Switched to {campus.name}.")
    next_url = request.POST.get("next") or ""
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect("enterprise:group_dashboard", slug=slug)


@group_required(*ALL_GROUP_ROLES)
@feature_required("groups")
def group_students(request, slug):
    """Student-level page. It never renders without an approved grant."""
    organisation = request.organisation
    campus = _selected_campus(request, organisation)
    if campus is None:
        raise Http404("This group has no campuses.")
    require_student_grant(request, campus, ("personal_data",))
    from academics.models import Student

    rows = Student.all_objects.filter(institution=campus).select_related("user")[:200]
    log_group_action(
        request, "group.student_view", organisation, institution=campus,
        detail={"campus_ids": [campus.pk]},
    )
    return render(
        request, "enterprise/group_students.html",
        {"organisation": organisation, "campus": campus, "students": rows},
    )


@group_required(GroupRole.ADMIN, GroupRole.FINANCE)
@feature_required("groups")
def group_request_access(request, slug):
    organisation = request.organisation
    campus = _selected_campus(request, organisation)
    if request.method == "POST":
        try:
            request_group_student_access(
                request.user, organisation, campus,
                request.POST.get("reason", ""),
                hours=request.POST.get("hours", 24),
                categories=request.POST.getlist("categories") or ("personal_data",),
                request=request,
            )
        except ValidationError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request, "Request sent. The campus administrator must approve it."
            )
            return redirect("enterprise:group_dashboard", slug=slug)
    return render(
        request, "enterprise/group_request_access.html",
        {"organisation": organisation, "campus": campus,
         "categories": ("personal_data", "finance", "results")},
    )


# ---------- shared settings ----------


@group_required(GroupRole.ADMIN)
@feature_required("groups")
def group_settings(request, slug):
    organisation = request.organisation
    rows = []
    for key, label in SHARED_SETTINGS.items():
        shared = organisation.shared_settings.filter(key=key).first()
        entry = {
            "key": key, "label": label, "value": shared.value if shared else {},
            "value_text": json.dumps(shared.value if shared else {}),
            "locked": shared.locked if shared else False,
        }
        entry["preview"] = setting_preview(organisation, key)
        rows.append(entry)
    return render(
        request, "enterprise/group_settings.html",
        {"organisation": organisation, "rows": rows},
    )


@require_POST
@group_required(GroupRole.ADMIN)
@feature_required("groups")
def group_setting_save(request, slug):
    key = request.POST.get("key", "")
    try:
        if key not in SETTING_KEYS:
            raise ValidationError("Unknown shared setting.")
        try:
            value = json.loads(request.POST.get("value", "{}"))
        except (TypeError, ValueError) as exc:
            raise ValidationError("The value must be valid JSON.") from exc
        save_shared_setting(
            request.organisation, key, value, actor=request.user,
            lock=request.POST.get("lock") == "on",
        )
    except ValidationError as exc:
        messages.error(request, str(exc))
        return redirect("enterprise:group_settings", slug=slug)
    log_group_action(
        request, "group.setting_saved", request.organisation,
        detail={"key": key},
    )
    messages.success(request, f"Group default for {SHARED_SETTINGS[key]} saved.")
    return redirect("enterprise:group_settings", slug=slug)


def _can_override_campus(user, organisation, campus):
    """A group admin or the campus's own administrator may set an override."""
    if GroupMembership.objects.filter(
        user=user, organisation=organisation, is_active=True, role=GroupRole.ADMIN
    ).exists():
        return True
    return (
        user.is_authenticated
        and user.institution_id == campus.pk
        and (
            user.is_superuser
            or user.role == "admin"
            or user.has_code("admin.manage_settings")
        )
    )


def campus_settings(request, slug, campus_id):
    if not request.user.is_authenticated:
        return redirect("enterprise:group_login", slug=slug)
    organisation = get_object_or_404(Organisation, slug=slug)
    campus = get_object_or_404(
        Institution, pk=campus_id, group_membership__organisation=organisation
    )
    if not _can_override_campus(request.user, organisation, campus):
        raise PermissionDenied("Only this campus's administrator may override a setting.")
    request.organisation = organisation
    request.institution = campus
    if not feature_enabled("groups", campus):
        raise Http404("Groups are not enabled for this campus.")
    rows = []
    for key, label in SHARED_SETTINGS.items():
        detail = effective_setting(organisation, campus, key)
        rows.append({**detail, "key": key, "label": label,
                     "value_text": json.dumps(detail["value"]),
                     "source_label": detail["label"]})
    return render(
        request, "enterprise/campus_settings.html",
        {"organisation": organisation, "campus": campus, "rows": rows},
    )


@require_POST
def campus_setting_save(request, slug, campus_id):
    if not request.user.is_authenticated:
        return redirect("enterprise:group_login", slug=slug)
    organisation = get_object_or_404(Organisation, slug=slug)
    campus = get_object_or_404(
        Institution, pk=campus_id, group_membership__organisation=organisation
    )
    if not _can_override_campus(request.user, organisation, campus):
        raise PermissionDenied("Only this campus's administrator may override a setting.")
    if not feature_enabled("groups", campus):
        raise Http404("Groups are not enabled for this campus.")
    key = request.POST.get("key", "")
    try:
        value = json.loads(request.POST.get("value", "{}"))
        save_campus_override(campus, key, value, actor=request.user)
    except (ValidationError, TypeError, ValueError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Campus setting saved.")
    return redirect("enterprise:campus_settings", slug=slug, campus_id=campus.pk)


# ---------- group billing ----------


@group_required(GroupRole.ADMIN, GroupRole.FINANCE)
@feature_required("groups")
def group_billing(request, slug):
    organisation = request.organisation
    if request.method == "POST" and request.POST.get("action") == "invoice":
        if request.group_membership.role != GroupRole.ADMIN:
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied("Only a group administrator can issue an invoice.")
        try:
            invoice = create_consolidated_invoice(
                organisation, actor=request.user, period=request.POST.get("period", "")
            )
        except ValidationError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Consolidated invoice {invoice.number} created.")
            return redirect("enterprise:group_billing", slug=slug)
    return render(
        request, "enterprise/group_billing.html",
        {
            "organisation": organisation,
            "billing": GroupBilling.objects.filter(organisation=organisation).first(),
            "invoices": organisation.group_invoices.prefetch_related("lines"),
        },
    )


# ---------- exports ----------


@group_required(*ALL_GROUP_ROLES)
@feature_required("groups")
def group_export(request, slug):
    dashboard = group_dashboard_data(request.organisation, **_dashboard_filters(request))
    fmt = request.GET.get("format", "xlsx")
    if fmt not in ("xlsx", "pdf", "csv"):
        fmt = "xlsx"
    headers = ["Campus", "Enrolment", "Collected", "Target", "Attendance %", "Pass %",
               "Staff", "Subscription"]
    data = [[
        row["campus"], row["enrolment"], str(row["collected"]), str(row["target"]),
        row["attendance"], row["pass_rate"], row["staff"], row["subscription"],
    ] for row in dashboard["rows"]]
    if fmt == "csv":
        import csv

        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(headers)
        writer.writerows(data)
        response = HttpResponse(out.getvalue(), content_type="text/csv; charset=utf-8")
    elif fmt == "xlsx":
        from openpyxl import Workbook
        from openpyxl.styles import Font

        book = Workbook()
        sheet = book.active
        sheet.title = "Campuses"
        sheet.append(headers)
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        for row in data:
            sheet.append(row)
        out = io.BytesIO()
        book.save(out)
        response = HttpResponse(
            out.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    else:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

        out = io.BytesIO()
        doc = SimpleDocTemplate(out, pagesize=A4, title=f"{request.organisation.name} group")
        table = Table([headers, *data], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E3A8A")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ]))
        doc.build([table, Paragraph(
            f"Generated {timezone.localdate().isoformat()} - aggregate figures only",
            getSampleStyleSheet()["Normal"],
        )])
        response = HttpResponse(out.getvalue(), content_type="application/pdf")
    filename = f"group-{request.organisation.slug}-{timezone.localdate().isoformat()}.{fmt}"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# =====================================================================
# Sponsor portal: platform administration
# =====================================================================


@platform_required("platform.manage_billing")
def sponsor_account_list(request):
    accounts = SponsorAccount.objects.select_related(
        "user", "sponsor", "organisation"
    ).order_by("sponsor__name", "user__username")
    return render(
        request, "enterprise/platform_sponsor_list.html", {"accounts": accounts}
    )


@platform_required("platform.manage_billing")
def sponsor_account_create(request):
    form = SponsorAccountForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        account = form.save(commit=False)
        sponsor = account.sponsor
        if not feature_enabled("sponsor_portal", sponsor.institution):
            form.add_error("sponsor", "The sponsor portal is not enabled for this school.")
        else:
            try:
                created = create_sponsor_account(
                    user=account.user, sponsor=sponsor, role=account.role,
                    organisation=account.organisation,
                    sharing_level=account.sharing_level,
                    consent_evidence=request.POST.get("consent_evidence", ""),
                    notify_email=account.notify_email,
                    notify_in_app=account.notify_in_app,
                    actor=request.user, request=request,
                )
            except ValidationError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(
                    request, f"Sponsor account created for {created.user.username}."
                )
                return redirect("enterprise:sponsor_account_detail", pk=created.pk)
    return render(request, "enterprise/platform_sponsor_form.html", {"form": form})


@platform_required("platform.manage_billing")
def sponsor_account_detail(request, pk):
    account = get_object_or_404(SponsorAccount, pk=pk)
    link_form = SponsorLinkForm(sponsor=account.sponsor)
    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action == "resend":
                resend_sponsor_verification(account, request=request)
            elif action == "toggle":
                account.is_active = not account.is_active
                account.save(update_fields=["is_active", "updated_at"])
                log_sponsor_action(
                    request, "sponsor.account_toggled", account,
                    {"kind": "active" if account.is_active else "inactive"},
                )
            elif action == "link":
                link_form = SponsorLinkForm(request.POST, sponsor=account.sponsor)
                if link_form.is_valid():
                    data = link_form.cleaned_data
                    create_sponsor_link(
                        account, data["student"], percentage=data["percentage"],
                        fixed_amount=data["fixed_amount"], valid_from=data["valid_from"],
                        valid_to=data["valid_to"], reference=data["reference"],
                        request=request,
                    )
        except ValidationError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Sponsor account updated.")
        return redirect("enterprise:sponsor_account_detail", pk=account.pk)
    commitments = sponsor_commitments(account, request=request)
    return render(
        request, "enterprise/platform_sponsor_detail.html",
        {
            "account": account,
            "commitments": commitments,
            "link_form": link_form,
            "sharing_labels": dict(SponsorAccount.SharingLevel.choices),
        },
    )


@platform_required("platform.view_institutions")
def sponsor_donor_report(request):
    """Scholarship and donor totals for one school, without student names."""
    raw = request.GET.get("institution", "")
    institution = (
        Institution.objects.filter(pk=int(raw)).first() if raw.isdigit() else None
    )
    if institution is None:
        institution = getattr(request, "institution", None) or request.user.institution
    start, end = _date_range(request)
    report = donor_report(institution, start, end)
    if request.GET.get("format") == "csv":
        import csv

        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["Sponsor", "Students", "Committed", "Scholarship", "Paid",
                         "Credit notes"])
        for row in report["rows"]:
            writer.writerow([
                row["sponsor"], row["students"], row["committed"],
                row["scholarship"], row["paid"], row["credit_note"],
            ])
        response = HttpResponse(out.getvalue(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            f'attachment; filename="donors-{timezone.localdate().isoformat()}.csv"'
        )
        return response
    return render(
        request, "enterprise/donor_report.html",
        {
            "report": report, "institution": institution,
            "institutions": Institution.objects.filter(is_active=True),
            "start": start, "end": end,
        },
    )


# =====================================================================
# Sponsor portal: named donor users
# =====================================================================


def _safe_next(request):
    """Only same-site paths may be used as a post-login destination."""
    value = request.POST.get("next") or request.GET.get("next") or ""
    if value and url_has_allowed_host_and_scheme(
        value, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return value
    return ""


def _date_range(request):
    """Read optional inclusive from/to dates without ever raising on bad input."""
    try:
        start = date.fromisoformat(request.GET["start"]) if request.GET.get("start") else None
        end = date.fromisoformat(request.GET["end"]) if request.GET.get("end") else None
    except ValueError:
        start = end = None
    return start, end


def _portal_destination(account):
    return (
        "enterprise:sponsor_verify" if not account.is_verified
        else "enterprise:sponsor_dashboard"
    )


def sponsor_login(request):
    """Sign in at /sponsor/ to one or more existing finance sponsors."""
    next_url = _safe_next(request)
    if request.method == "POST":
        form = IdentifierAuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            eligible = active_sponsor_accounts_for(user)
            if not eligible:
                form.add_error(None, "This account has no active sponsor portal invitation.")
            else:
                login(request, user)
                request.session.pop("sponsor_account_id", None)
                if len(eligible) > 1:
                    request.session["sponsor_next"] = next_url
                    return redirect("enterprise:sponsor_select")
                account = eligible[0]
                request.session["sponsor_account_id"] = account.pk
                return redirect(next_url or _portal_destination(account))
    else:
        form = IdentifierAuthenticationForm(request)
    return render(
        request, "enterprise/sponsor_login.html",
        {"form": form, "next_url": next_url},
    )


def sponsor_select(request):
    """Choose which sponsor to represent when one user has several invitations."""
    if not request.user.is_authenticated:
        return redirect("enterprise:sponsor_login")
    accounts = active_sponsor_accounts_for(request.user)
    if not accounts:
        raise PermissionDenied("You do not have an active sponsor portal account.")
    if request.method == "POST":
        raw = request.POST.get("account", "")
        chosen = next(
            (a for a in accounts if str(a.pk) == raw), None
        )
        if chosen is None:
            messages.error(request, "Choose one of your sponsors.")
        else:
            request.session["sponsor_account_id"] = chosen.pk
            target = request.session.pop("sponsor_next", "")
            return redirect(target or _portal_destination(chosen))
    return render(
        request, "enterprise/sponsor_select.html", {"accounts": accounts}
    )


def sponsor_verify(request):
    """Enter the one-time code mailed with the sponsor invitation."""
    if not request.user.is_authenticated:
        return redirect("enterprise:sponsor_login")
    accounts = active_sponsor_accounts_for(request.user)
    if not accounts:
        raise PermissionDenied("You do not have an active sponsor portal account.")
    selected = request.session.get("sponsor_account_id")
    account = next((a for a in accounts if a.pk == selected), None) or accounts[0]
    if account.is_verified:
        return redirect("enterprise:sponsor_dashboard")
    if request.method == "POST":
        try:
            verified = verify_sponsor_account(
                request.user, request.POST.get("token", ""), request=request
            )
        except ValidationError as exc:
            messages.error(request, str(exc))
        else:
            request.session["sponsor_account_id"] = verified.pk
            messages.success(request, "Sponsor account verified. Welcome.")
            return redirect("enterprise:sponsor_dashboard")
    return render(
        request, "enterprise/sponsor_verify.html", {"account": account}
    )


@require_POST
def sponsor_logout(request):
    request.session.pop("sponsor_account_id", None)
    request.session.pop("sponsor_next", None)
    logout(request)
    return redirect("enterprise:sponsor_login")


@sponsor_required()
def sponsor_dashboard(request):
    """Totals only; named detail is opened from the statement when consent allows."""
    account = request.sponsor_account
    return render(
        request, "enterprise/sponsor_dashboard.html",
        {
            "account": account,
            "commitments": sponsor_commitments(account, request=request),
            "statement": sponsor_statement(account, request=request),
            "has_named_access": account.allows(3) and student_level_grant(
                account.user, request.institution,
                ("personal_data", "finance"), request=request,
            ) is not None,
        },
    )


@sponsor_required()
def sponsor_invoices(request):
    """Sponsored invoices and the amount each sponsorship can cover."""
    start, end = _date_range(request)
    return render(
        request, "enterprise/sponsor_invoices.html",
        {
            "account": request.sponsor_account,
            "commitments": sponsor_commitments(
                request.sponsor_account, start, end, request=request
            ),
            "start": start, "end": end,
            "can_pay": request.sponsor_account.role in SPONSOR_WRITE_ROLES,
        },
    )


@sponsor_required()
def sponsor_statement_view(request):
    start, end = _date_range(request)
    return render(
        request, "enterprise/sponsor_statement.html",
        {
            "account": request.sponsor_account,
            "statement": sponsor_statement(
                request.sponsor_account, start, end, request=request
            ),
            "start": start, "end": end,
        },
    )


@sponsor_required()
def sponsor_receipts_view(request):
    start, end = _date_range(request)
    return render(
        request, "enterprise/sponsor_receipts.html",
        {
            "account": request.sponsor_account,
            "receipts": sponsor_receipts(
                request.sponsor_account, start, end, request=request
            ),
            "start": start, "end": end,
        },
    )


@sponsor_required()
def sponsor_receipt_pdf(request, pk):
    """A sponsor receipt, redacted unless level 3 and a live grant are in force."""
    account = request.sponsor_account
    school = account.sponsor.institution
    with use_institution(school):
        payment = get_object_or_404(Payment.all_objects, pk=pk, institution=school)
    if not any(
        row["payment_id"] == payment.pk
        for row in sponsor_receipts(account, request=request)
    ):
        raise Http404("This payment is not on your sponsor statement.")
    named = account.allows(3) and student_level_grant(
        account.user, school, ("personal_data", "finance"), request=request,
    ) is not None
    student_name = payment.invoice.student.name if named else None
    data = build_sponsor_receipt_pdf(
        account, payment, request=request, student_name=student_name
    )
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="sponsor-receipt-{payment.receipt_no or payment.pk}.pdf"'
    )
    return response


@sponsor_required()
def sponsor_export(request):
    """Statement and commitment export using only sharing-safe labels."""
    start, end = _date_range(request)
    statement = sponsor_statement(request.sponsor_account, start, end, request=request)
    commitments = sponsor_commitments(request.sponsor_account, start, end, request=request)
    fmt = request.GET.get("format", "csv")
    if fmt not in ("csv", "xlsx", "pdf"):
        fmt = "csv"
    headers = ["Date", "Kind", "Reference", "Student", "Amount"]
    data = [[
        str(row["date"]), row["kind"], row["reference"], row["label"], str(row["amount"]),
    ] for row in statement["rows"]]
    if fmt == "csv":
        import csv

        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(headers)
        writer.writerows(data)
        response = HttpResponse(out.getvalue(), content_type="text/csv; charset=utf-8")
    elif fmt == "xlsx":
        from openpyxl import Workbook

        book = Workbook()
        sheet = book.active
        sheet.title = "Sponsor statement"
        sheet.append(headers)
        for row in data:
            sheet.append(row)
        out = io.BytesIO()
        book.save(out)
        response = HttpResponse(
            out.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    else:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

        out = io.BytesIO()
        doc = SimpleDocTemplate(out, pagesize=A4, title="Sponsor statement")
        table = Table([headers, *data], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E3A8A")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ]))
        doc.build([table, Paragraph(
            f"Committed {commitments['totals']['coverage']} - "
            f"sharing level {request.sponsor_account.sharing_level}",
            getSampleStyleSheet()["Normal"],
        )])
        response = HttpResponse(out.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="sponsor-statement-'
        f'{timezone.localdate().isoformat()}.{fmt}"'
    )
    return response


@require_POST
@sponsor_required(*SPONSOR_WRITE_ROLES, write=True)
def sponsor_pay(request):
    """Post a sponsor payment, capped by the sponsorship and invoice balance."""
    account = request.sponsor_account
    require_sponsor_sensitive_action(account, request)
    try:
        payment, credit = allocate_sponsor_payment(
            account, request.POST.get("invoice"),
            amount=request.POST.get("amount"),
            method=request.POST.get("method"),
            request=request,
        )
    except (ValidationError, InvalidOperation, TypeError) as exc:
        messages.error(request, str(exc))
    else:
        if credit is not None:
            messages.warning(
                request, f"Payment recorded with {credit.amount:.2f} held as credit."
            )
        else:
            messages.success(request, f"Payment {payment.receipt_no} recorded.")
    return redirect("enterprise:sponsor_invoices")


@require_POST
@sponsor_required(*SPONSOR_WRITE_ROLES, write=True)
def sponsor_credit_note(request):
    """Issue an immutable finance credit note for a sponsored invoice."""
    account = request.sponsor_account
    require_sponsor_sensitive_action(account, request)
    try:
        waiver = issue_sponsor_credit_note(
            account, request.POST.get("invoice"),
            amount=request.POST.get("amount"),
            reason=request.POST.get("reason", ""),
            request=request,
        )
    except (ValidationError, InvalidOperation, TypeError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Credit note {waiver.note_no} issued.")
    return redirect("enterprise:sponsor_statement")


@sponsor_required()
def sponsor_settings(request):
    """Consent, sharing level and notification choices for one sponsor user."""
    account = request.sponsor_account
    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action == "sharing":
                set_sharing_level(
                    account, request.POST.get("level"),
                    request.POST.get("evidence", ""), request=request,
                )
            elif action == "withdraw":
                withdraw_sponsor_sharing(
                    account, request.POST.get("reason", ""), request=request
                )
            elif action == "notify":
                account.notify_email = "notify_email" in request.POST
                account.notify_in_app = "notify_in_app" in request.POST
                account.save(update_fields=["notify_email", "notify_in_app", "updated_at"])
                log_sponsor_action(
                    request, "sponsor.notifications_changed", account,
                    {"notify_email": account.notify_email,
                     "notify_in_app": account.notify_in_app},
                )
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Sponsor settings saved.")
        return redirect("enterprise:sponsor_settings")
    return render(
        request, "enterprise/sponsor_settings.html",
        {
            "account": account,
            "levels": [
                choice for choice in SponsorAccount.SharingLevel.choices
                if choice[0] != SponsorAccount.SharingLevel.WITHDRAWN
            ],
        },
    )


@sponsor_required()
def sponsor_security(request):
    """Authenticator setup and password re-check for sensitive sponsor actions."""
    account = request.sponsor_account
    form = TwoFactorForm(request.POST or None)
    reauth_form = PasswordConfirmForm(request.POST or None)
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "start":
            account.two_factor_secret = new_secret()
            account.save(update_fields=["two_factor_secret", "updated_at"])
            log_sponsor_action(request, "sponsor.two_factor_started", account)
            messages.info(request, "Scan the code, then enter one code to switch 2FA on.")
            return redirect("enterprise:sponsor_security")
        if action == "enable" and form.is_valid():
            if not account.two_factor_secret:
                form.add_error("code", "Start the setup first.")
            elif not verify_totp(account.two_factor_secret, form.cleaned_data["code"]):
                form.add_error("code", "That code did not match. Try the next one.")
            else:
                account.two_factor_enabled = True
                account.save(update_fields=["two_factor_enabled", "updated_at"])
                mark_reauth(request, account)
                log_sponsor_action(request, "sponsor.two_factor_enabled", account)
                messages.success(request, "Two-factor authentication is on.")
                return redirect("enterprise:sponsor_security")
        elif action == "disable":
            account.two_factor_enabled = False
            account.save(update_fields=["two_factor_enabled", "updated_at"])
            log_sponsor_action(request, "sponsor.two_factor_disabled", account)
            messages.warning(request, "Two-factor authentication is off.")
            return redirect("enterprise:sponsor_security")
        elif action == "reauth" and reauth_form.is_valid():
            if request.user.check_password(reauth_form.cleaned_data["password"]):
                mark_reauth(request, account)
                log_sponsor_action(request, "sponsor.reauthenticated", account)
                messages.success(request, "Password confirmed for the next 15 minutes.")
                return redirect("enterprise:sponsor_security")
            reauth_form.add_error("password", "That password is not correct.")
    uri = (
        provisioning_uri(account.two_factor_secret, request.user.email)
        if account.two_factor_secret and not account.two_factor_enabled else ""
    )
    return render(
        request, "enterprise/sponsor_security.html",
        {
            "account": account, "form": form, "reauth_form": reauth_form,
            "uri": uri,
            "secret": account.two_factor_secret if not account.two_factor_enabled else "",
        },
    )


# =====================================================================
# Reseller accounts and portal
# =====================================================================


@platform_required("platform.manage_billing")
def reseller_account_list(request):
    accounts = ResellerAccount.objects.select_related("user", "organisation").order_by(
        "organisation__name", "user__username"
    )
    commissions = ResellerCommission.objects.select_related(
        "account__organisation", "institution"
    )[:50]
    return render(
        request, "enterprise/reseller_account_list.html",
        {"accounts": accounts, "commissions": commissions},
    )


@platform_required("platform.manage_billing")
def reseller_account_create(request):
    form = ResellerAccountForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            account = create_reseller_account(
                user=data["user"], organisation=data["organisation"],
                role=data["role"], commission_rate=data["commission_rate"],
                actor=request.user, request=request,
            )
        except ValidationError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"Reseller account created for {account.user.username}.")
            return redirect("enterprise:reseller_account_list")
    return render(
        request, "enterprise/reseller_account_form.html",
        {"form": form},
    )


@platform_required("platform.manage_billing")
def reseller_commission_update(request, pk):
    if request.method != "POST":
        raise Http404
    commission = get_object_or_404(ResellerCommission, pk=pk)
    if request.POST.get("action") == "pay":
        mark_commission_paid(commission, actor=request.user, request=request)
        messages.success(request, "Commission marked as paid.")
    return redirect("enterprise:reseller_account_list")


def reseller_login(request, slug):
    """Sign in to one reseller organisation at /reseller/<slug>/login/."""
    org = get_object_or_404(
        Organisation, slug=slug, status=Organisation.Status.ACTIVE,
        type=Organisation.Type.RESELLER,
    )
    if not feature_enabled_for_organisation(org, "reseller"):
        raise Http404("Reseller access is not enabled for this organisation.")
    next_url = _safe_next(request)
    form = IdentifierAuthenticationForm(request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.get_user()
        account = next(
            (a for a in reseller_accounts_for(user) if a.organisation_id == org.pk), None
        )
        if account is None:
            logout(request)
            form.add_error(None, "Use a reseller account for this organisation.")
        else:
            login(request, user)
            request.session["reseller_account_id"] = account.pk
            request.session.pop("reseller_client_id", None)
            if next_url:
                return redirect(next_url)
            return redirect("enterprise:reseller_dashboard", slug=org.slug)
    return render(
        request, "enterprise/reseller_login.html",
        {"organisation": org, "form": form, "next_url": next_url},
    )


@reseller_required()
def reseller_dashboard(request, slug):
    """Client aggregates and commission totals; no student-level data."""
    account = request.reseller_account
    client_id = request.GET.get("client") or ""
    data = reseller_portal_data(account, client_id or None)
    return render(
        request, "enterprise/reseller_dashboard.html",
        {"organisation": request.organisation, "account": account,
         "data": data, "client_id": client_id},
    )


@reseller_required()
def reseller_export(request, slug):
    """CSV export of the same aggregates shown on the reseller dashboard."""
    import csv

    data = reseller_portal_data(request.reseller_account, request.GET.get("client") or None)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Campus", "Enrolment", "Collected", "Subscription"])
    for row in data["rows"]:
        writer.writerow([row["campus"], row["enrolment"], row["collected"],
                         row["subscription"]])
    response = HttpResponse(out.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="reseller-{slug}-{timezone.localdate().isoformat()}.csv"'
    )
    return response


@require_POST
@reseller_required()
def reseller_logout(request, slug):
    request.session.pop("reseller_account_id", None)
    request.session.pop("reseller_client_id", None)
    logout(request)
    return redirect("enterprise:reseller_login", slug=slug)
