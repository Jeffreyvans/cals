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
from enterprise.access import (
    ALL_GROUP_ROLES,
    GroupRole,
    feature_enabled,
    feature_required,
    group_required,
    log_group_action,
    require_student_grant,
)
from enterprise.models import (
    GroupBilling,
    GroupMembership,
    Organisation,
)
from enterprise.services import (
    assign_group_role,
    attach_institution,
    create_consolidated_invoice,
    create_organisation,
    detach_institution,
    effective_setting,
    refresh_group_alerts,
    request_group_student_access,
    revoke_group_role,
    save_campus_override,
    save_shared_setting,
    setting_preview,
)
from enterprise.services import (
    group_dashboard as group_figures,
)
from enterprise.settings_registry import SETTING_KEYS, SHARED_SETTINGS
from platform_admin.access import platform_required

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


def feature_enabled_for_organisation(org):
    """Any attached campus with the group feature enabled allows group sign-in."""
    from enterprise.access import feature_enabled

    return any(feature_enabled("groups", campus) for campus in org.campuses())


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
