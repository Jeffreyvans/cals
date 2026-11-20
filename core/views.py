"""Screens for the foundation: settings, roles, notifications and the audit log."""

import csv
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from core.access import user_has_code
from core.audit import log_action
from core.models import (
    AuditEntry,
    Institution,
    Notification,
    Role,
    RolePermission,
    SensitiveViewLog,
)
from core.modules import ALL_MODULES, is_enabled, set_module
from core.permissions import PERMISSIONS
from core.roles import ensure_permissions, ensure_roles
from core.terminology import terminology_for

PERM_CATEGORIES = []
for _code, _desc, _cat in PERMISSIONS:
    if _cat not in PERM_CATEGORIES:
        PERM_CATEGORIES.append(_cat)


def service_worker(request):
    """Serve the worker from the site root so it can control every page."""
    path = Path(settings.BASE_DIR) / "static" / "js" / "sw.js"
    try:
        body = path.read_text(encoding="utf-8")
    except OSError:  # a source checkout without the file must not 500
        body = "/* service worker unavailable */\n"
    response = HttpResponse(body, content_type="application/javascript")
    response["Cache-Control"] = "no-cache"
    response["Service-Worker-Allowed"] = "/"
    return response


def offline(request):
    """The small page the service worker shows when the network is gone."""
    response = render(request, "offline.html")
    response["Cache-Control"] = "public, max-age=3600"
    return response


def billing_contact(request):
    """Billing contact details, reachable even when an institution is suspended."""
    return render(request, "errors/billing_contact.html")


def _require(request, code):
    """Stop the request unless the user holds the permission code."""
    if not user_has_code(request.user, code):
        raise PermissionDenied("Your role does not allow this page.")


def _institution(request):
    inst = getattr(request, "institution", None)
    if inst is None:
        inst = Institution.objects.filter(slug="default").first()
    return inst


# ---------- institution settings ----------
@login_required
def settings_view(request):
    """Details, branding and the module switches for this institution."""
    _require(request, "admin.manage_settings")
    inst = _institution(request)
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "details":
            inst.name = request.POST.get("name", inst.name)[:200]
            inst.tagline = request.POST.get("tagline", inst.tagline)[:200]
            inst.about = request.POST.get("about", inst.about)[:600]
            inst.website = request.POST.get("website", inst.website)[:200]
            inst.currency = request.POST.get("currency", inst.currency)[:10]
            inst.timezone = request.POST.get("timezone", inst.timezone)[:64]
            inst.language = request.POST.get("language", inst.language)[:10]
            inst.address = request.POST.get("address", "")[:2000]
            inst.contact_email = request.POST.get("contact_email", "")[:200]
            inst.contact_phone = request.POST.get("contact_phone", "")[:30]
            inst.save()
            log_action(request.user, "institution.updated", inst, request=request)
            messages.success(request, "Institution details saved.")
            return redirect("institution_settings")
        if action == "branding":
            _require(request, "admin.manage_branding")
            from core.logo import remove_logo, save_logo

            if request.POST.get("remove_logo"):
                remove_logo(inst)
                log_action(request.user, "institution.logo_removed", inst,
                           request=request)
                messages.success(request, "The logo was removed.")
                return redirect("institution_settings")
            inst.primary_color = request.POST.get("primary_color", inst.primary_color)[:20]
            inst.accent_color = request.POST.get("accent_color", inst.accent_color)[:20]
            inst.save(update_fields=["primary_color", "accent_color"])
            upload = request.FILES.get("logo")
            if upload is not None:
                try:
                    save_logo(inst, upload)
                except ValidationError as exc:
                    for problem in exc.messages:
                        messages.error(request, problem)
                    return redirect("institution_settings")
            log_action(request.user, "institution.branding_updated", inst,
                       after={"primary_color": inst.primary_color,
                              "accent_color": inst.accent_color,
                              "logo": bool(inst.logo)},
                       request=request)
            messages.success(request, "Branding saved.")
            return redirect("institution_settings")
        if action == "vat":
            inst.vat_status = request.POST.get("vat_status", inst.vat_status)[:20]
            inst.vat_number = request.POST.get("vat_number", "")[:40]
            inst.bp_number = request.POST.get("bp_number", "")[:40]
            inst.vat_address = request.POST.get("vat_address", "")[:2000]
            inst.base_currency = request.POST.get(
                "base_currency", inst.base_currency)[:10]
            inst.save(update_fields=[
                "vat_status", "vat_number", "bp_number", "vat_address",
                "base_currency"])
            log_action(request.user, "institution.vat_updated", inst,
                       after={"vat_status": inst.vat_status,
                              "vat_number": inst.vat_number,
                              "bp_number": inst.bp_number},
                       request=request)
            messages.success(request, "VAT settings saved.")
            return redirect("institution_settings")
        if action == "registration":
            from accounts.registration import STAFF_POSITIONS

            inst.public_page_enabled = (
                request.POST.get("public_page_enabled") == "on"
            )
            inst.show_register_button = (
                request.POST.get("show_register_button") == "on"
            )
            inst.public_registration = (
                request.POST.get("public_registration") == "on"
            )
            chosen = set(request.POST.getlist("staff_positions"))
            inst.staff_positions = [
                code for code in STAFF_POSITIONS if code in chosen
            ]
            inst.save(update_fields=[
                "public_page_enabled",
                "show_register_button",
                "public_registration",
                "staff_positions",
            ])
            log_action(
                request.user,
                "institution.registration_updated",
                inst,
                after={
                    "public_page_enabled": inst.public_page_enabled,
                    "show_register_button": inst.show_register_button,
                    "public_registration": inst.public_registration,
                    "staff_positions": inst.staff_positions,
                },
                request=request,
            )
            messages.success(request, "Public page and registration settings saved.")
            return redirect("institution_settings")
        if action == "module":
            code = request.POST.get("code", "")
            if code in ALL_MODULES:
                on = request.POST.get("enabled") == "on"
                set_module(inst, code, on)
                log_action(request.user, "institution.module_changed", inst,
                           after={"code": code, "enabled": on}, request=request)
                messages.success(request, f"{code} switched {'on' if on else 'off'}.")
                return redirect("institution_settings")
        if action == "seed_roles":
            ensure_permissions()
            ensure_roles(inst)
            log_action(request.user, "institution.roles_seeded", inst, request=request)
            messages.success(request, "Default roles restored.")
            return redirect("institution_settings")
        if action == "terminology":
            from core.terminology import preset_labels, save_terminology

            preset = request.POST.get("preset", "")
            labels = preset_labels(preset, inst.type) or {}
            labels.update({
                field: request.POST.get(field, "")
                for field, _label in terminology_fields()
            })
            _record, changed = save_terminology(inst, labels)
            log_action(request.user, "institution.terminology_updated", inst,
                       after={"changed": changed}, request=request)
            messages.success(request, "The words this institution uses were saved.")
            return redirect("institution_settings")
        if action == "numbering":
            from core.numbering import save_format

            kind = request.POST.get("kind", "")
            try:
                save_format(
                    inst, kind,
                    prefix=request.POST.get("prefix", ""),
                    include_year=request.POST.get("include_year") == "on",
                    padding=request.POST.get("padding", ""),
                    next_value=request.POST.get("next", ""),
                    year_digits=request.POST.get("year_digits") or None,
                    separator=request.POST.get("separator"),
                    check_mode=request.POST.get("check_mode") or None,
                    check_letter=request.POST.get("check_letter", ""),
                    include_programme=request.POST.get("include_programme"),
                    include_department=request.POST.get("include_department"),
                    reset_yearly=request.POST.get("reset_yearly"),
                    start_number=request.POST.get("start_number") or None,
                    actor=request.user,
                    reason=request.POST.get("reason", ""),
                    request=request,
                )
            except ValidationError as exc:
                for problem in exc.messages:
                    messages.error(request, problem)
                return redirect("institution_settings")
            messages.success(request, f"{kind.title()} number format saved.")
            return redirect("institution_settings")
    switches = []
    for code in ALL_MODULES:
        switches.append({"code": code, "on": is_enabled(code, inst)})
    terms = terminology_for(inst)
    term_fields = [
        {"field": field, "label": label, "value": terms.get(field, "")}
        for field, label in terminology_fields()
    ]
    from accounts.registration import POSITION_ORDER, STAFF_POSITIONS

    chosen_positions = set(inst.staff_positions or [])
    staff_position_choices = [
        {
            "code": code,
            "label": STAFF_POSITIONS[code].label,
            "checked": code in chosen_positions,
        }
        for code in POSITION_ORDER
    ]
    from core.numbering import formats_for

    number_formats = formats_for(inst)
    return render(request, "core/institution_settings.html", {
        "inst": inst,
        "switches": switches,
        "modules": ALL_MODULES,
        "terms": terms,
        "term_fields": term_fields,
        "staff_position_choices": staff_position_choices,
        "number_formats": number_formats,
        "vat_status_choices": Institution.VatStatus.choices,
        "can_brand": user_has_code(request.user, "admin.manage_branding"),
        "institution_login_url": request.build_absolute_uri(
            reverse("institution_login", args=[inst.slug])
        ),
    })


# ---------- numbering ----------
@login_required
def numbering_settings(request):
    """The dedicated student, staff and applicant number settings screen.

    The format is stored per institution and the last issued number per
    institution, kind and year, so one institution's sequence can never move
    another's. Changing a format only affects numbers issued from then on.
    """
    _require(request, "admin.manage_settings")
    inst = _institution(request)
    if request.method == "POST":
        action = request.POST.get("action", "format")
        kind = request.POST.get("kind", "")
        try:
            if action == "override":
                from core.numbering import manual_override

                manual_override(
                    inst, kind,
                    request.POST.get("current", ""),
                    request.POST.get("number", ""),
                    actor=request.user, request=request)
                messages.success(
                    request, "The number was changed and the change was logged.")
            else:
                from core.numbering import save_format

                save_format(
                    inst, kind,
                    prefix=request.POST.get("prefix", ""),
                    include_year=request.POST.get("include_year") == "on",
                    padding=request.POST.get("padding", ""),
                    next_value=request.POST.get("next", ""),
                    year_digits=request.POST.get("year_digits") or None,
                    separator=request.POST.get("separator"),
                    include_programme=request.POST.get("include_programme"),
                    include_department=request.POST.get("include_department"),
                    check_mode=request.POST.get("check_mode") or None,
                    check_letter=request.POST.get("check_letter", ""),
                    reset_yearly=request.POST.get("reset_yearly"),
                    start_number=request.POST.get("start_number") or None,
                    actor=request.user,
                    reason=request.POST.get("reason", ""),
                    request=request,
                )
                messages.success(
                    request, f"{kind.title()} number format saved. Existing "
                             "numbers were not changed.")
        except ValidationError as exc:
            for problem in exc.messages:
                messages.error(request, problem)
        return redirect("numbering_settings")

    from core.models import NumberFormat, NumberFormatChange
    from core.numbering import formats_for

    return render(request, "core/numbering_settings.html", {
        "inst": inst,
        "formats": formats_for(inst),
        "kinds": [{"code": code, "label": label}
                  for code, label in NumberFormat.Kind.choices],
        "separator_choices": [
            ("", "None"), ("-", "Hyphen"), ("/", "Slash")],
        "check_choices": [
            ("none", "No check letter"),
            ("fixed", "Fixed final letter"),
            ("calculated", "Calculated check letter"),
        ],
        "changes": NumberFormatChange.all_objects.filter(
            institution=inst).select_related("actor")[:20],
    })


def terminology_fields():
    from core.terminology import FIELDS

    return FIELDS


# ---------- roles and permissions ----------
@login_required
def role_list(request):
    """The roles of this institution and the permission matrix behind them."""
    _require(request, "admin.manage_roles")
    inst = _institution(request)
    ensure_permissions()
    roles = list(Role.objects.filter(institution=inst).prefetch_related("role_permissions"))
    perms = list(PERMISSIONS)
    for role in roles:
        role.held_codes = set(role.role_permissions.values_list("permission__code", flat=True))
        role.held_count = len(role.held_codes)
    categories = []
    for code, desc, cat in perms:
        for entry in categories:
            if entry["name"] == cat:
                entry["perms"].append((code, desc))
                break
        else:
            categories.append({"name": cat, "perms": [(code, desc)]})
    return render(request, "core/role_list.html", {
        "inst": inst,
        "roles": roles,
        "categories": categories,
    })


@login_required
def role_matrix(request):
    """Save the permission matrix."""
    _require(request, "admin.manage_roles")
    inst = _institution(request)
    if request.method == "POST":
        from core.models import Permission

        perms = {p.code: p for p in Permission.objects.all()}
        changed = 0
        for role in Role.objects.filter(institution=inst):
            for code in perms:
                wanted = request.POST.get(f"p_{role.pk}_{code}") == "on"
                existing = RolePermission.objects.filter(
                    role=role, permission=perms[code]
                ).exists()
                if wanted and not existing:
                    RolePermission.objects.create(role=role, permission=perms[code])
                    changed += 1
                elif not wanted and existing:
                    RolePermission.objects.filter(role=role, permission=perms[code]).delete()
                    changed += 1
        log_action(request.user, "roles.matrix_saved", inst,
                   after={"changes": changed}, request=request)
        messages.success(request, f"Permissions saved ({changed} change{'s' if changed != 1 else ''}).")
        return redirect("role_list")
    return redirect("role_list")


@login_required
def role_create(request):
    """Add a role of this institution."""
    _require(request, "admin.manage_roles")
    inst = _institution(request)
    if request.method == "POST":
        name = request.POST.get("name", "").strip()[:80]
        code = request.POST.get("code", "").strip()[:60]
        if not name:
            messages.error(request, "Give the role a name.")
        elif Role.objects.filter(institution=inst, code=code).exists():
            messages.error(request, f"The code '{code}' is already used.")
        else:
            role = Role.objects.create(
                institution=inst, name=name, code=code,
                description=request.POST.get("description", "")[:200],
            )
            log_action(request.user, "role.created", role, request=request)
            messages.success(request, f"Role {role.name} added.")
            return redirect("role_list")
    return render(request, "core/role_form.html", {"inst": inst})


@login_required
def role_detail(request, pk):
    """Rename a role or change its description."""
    _require(request, "admin.manage_roles")
    inst = _institution(request)
    role = get_object_or_404(Role, pk=pk, institution=inst)
    if request.method == "POST":
        role.name = request.POST.get("name", role.name)[:80]
        role.description = request.POST.get("description", role.description)[:200]
        role.save(update_fields=["name", "description"])
        log_action(request.user, "role.updated", role, request=request)
        messages.success(request, "Role saved.")
        return redirect("role_list")
    return render(request, "core/role_form.html", {"inst": inst, "role": role})


@login_required
def role_delete(request, pk):
    """Remove a role that is not one of the built-in ones."""
    _require(request, "admin.manage_roles")
    inst = _institution(request)
    role = get_object_or_404(Role, pk=pk, institution=inst)
    if role.is_system:
        messages.error(request, "Built-in roles cannot be deleted.")
        return redirect("role_list")
    if request.method == "POST":
        log_action(request.user, "role.deleted", role, request=request)
        role.delete()
        messages.success(request, "Role removed.")
        return redirect("role_list")
    return render(request, "core/confirm_delete.html", {
        "inst": inst, "title": f"Remove the role {role.name}?",
        "detail": "Nobody will hold this role afterwards.",
        "cancel": reverse("role_list"),
    })


# ---------- notifications ----------
@login_required
def notifications(request):
    """The signed-in user's notifications."""
    rows = Notification.objects.filter(user=request.user).select_related("institution")
    unread = [n for n in rows if n.read_at is None]
    if request.method == "POST":
        from core.notifications import mark_all_read, mark_read

        if request.POST.get("action") == "read_all":
            mark_all_read(request.user)
            messages.success(request, "All notifications marked as read.")
            return redirect("notifications")
        nid = request.POST.get("notification")
        if nid:
            mark_read(request.user, nid)
            return redirect(request.POST.get("next") or "notifications")
    return render(request, "core/notifications.html", {
        "rows": list(rows)[:100], "unread": len(unread),
    })


# ---------- audit ----------
@login_required
def audit_log(request):
    """Read the audit trail. Every row is a record of somebody's action."""
    _require(request, "admin.view_audit")
    inst = _institution(request)
    action = request.GET.get("action", "")
    date_from = request.GET.get("from", "").strip()
    date_to = request.GET.get("to", "").strip()
    rows = AuditEntry.objects.filter(institution=inst)
    if action:
        rows = rows.filter(action=action)
    if date_from:
        rows = rows.filter(created__date__gte=date_from)
    if date_to:
        rows = rows.filter(created__date__lte=date_to)
    rows = rows.select_related("actor")[:300]
    actions = sorted(set(AuditEntry.objects.filter(institution=inst)
                         .values_list("action", flat=True)))
    return render(request, "core/audit_log.html", {
        "inst": inst, "rows": rows, "action": action, "actions": actions,
        "date_from": date_from, "date_to": date_to,
    })


@login_required
def audit_export(request):
    """Download the audit trail as a CSV file."""
    _require(request, "admin.view_audit")
    inst = _institution(request)
    rows = AuditEntry.objects.filter(institution=inst).select_related("actor")
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = 'attachment; filename="audit-log.csv"'
    resp.write("\ufeff")
    w = csv.writer(resp)
    w.writerow(["When", "Who", "Action", "Model", "Object", "IP"])
    for r in rows:
        w.writerow([r.created.strftime("%Y-%m-%d %H:%M"), r.actor_name, r.action,
                    r.model_name, r.object_id, r.ip_address or ""])
    return resp


@login_required
def sensitive_log(request):
    """Who opened a restricted record."""
    _require(request, "admin.view_audit")
    inst = _institution(request)
    rows = SensitiveViewLog.objects.filter(institution=inst).select_related("actor")[:300]
    return render(request, "core/sensitive_log.html", {"inst": inst, "rows": rows})

def _number_pks(queryset, field, query, limit=20):
    """Primary keys whose number matches the query ignoring case/separators."""
    from core.numbering import matching_pks

    return matching_pks(queryset, field, query, limit=limit)


# ---------- global search ----------
@login_required
def global_search(request):
    """One search box for the records staff look up most often.

    Every result group is permission-filtered, so a bursar sees money and a
    registrar sees applications without either seeing the other's records.
    """
    _require(request, "search.global")
    query = request.GET.get("q", "").strip()
    groups = []
    if len(query) >= 2:
        from django.db.models import Q

        from academics.models import Course, Student
        from accounts.models import User
        from admissions.models import Application
        from finance.models import Payment

        if user_has_code(request.user, "students.view_all"):
            number_pks = _number_pks(Student.objects.all(), "student_no", query)
            rows = (Student.objects.select_related(
                        "user", "programme", "school_class")
                    .filter(Q(student_no__icontains=query)
                            | Q(pk__in=number_pks)
                            | Q(user__first_name__icontains=query)
                            | Q(user__last_name__icontains=query)
                            | Q(user__username__icontains=query))[:20])
            groups.append({"title": "Students", "items": [{
                "label": s.name,
                "meta": f"{s.student_no} - {s.programme.name}",
                "url": reverse("student_detail", args=[s.pk]),
            } for s in rows]})

        if (user_has_code(request.user, "admin.manage_users")
                or user_has_code(request.user, "hr.manage")):
            staff_numbers = _number_pks(
                User.tenant_objects.exclude(staff_profile__isnull=True),
                "staff_profile__employee_no", query)
            rows = (User.tenant_objects.select_related(
                        "primary_role", "staff_profile")
                    .filter(Q(first_name__icontains=query)
                            | Q(last_name__icontains=query)
                            | Q(username__icontains=query)
                            | Q(email__icontains=query)
                            | Q(staff_profile__employee_no__icontains=query)
                            | Q(pk__in=staff_numbers))[:20])
            groups.append({"title": "Staff accounts", "items": [{
                "label": u.get_full_name() or u.username,
                "meta": " · ".join(filter(None, [
                    getattr(getattr(u, "staff_profile", None),
                            "employee_no", ""),
                    u.username, u.get_role_display()])),
                "url": reverse("staff_detail", args=[u.pk]),
            } for u in rows]})

        if user_has_code(request.user, "academics.view_reports"):
            rows = Course.objects.select_related("programme").filter(
                Q(code__icontains=query) | Q(title__icontains=query))[:20]
            groups.append({"title": "Courses", "items": [{
                "label": f"{c.code} - {c.title}",
                "meta": c.programme.name,
                "url": reverse("course_roster", args=[c.pk]),
            } for c in rows]})

        if user_has_code(request.user, "finance.view_reports"):
            receipt_pks = _number_pks(Payment.objects.all(), "receipt_no", query)
            rows = (Payment.objects.select_related("invoice__student__user")
                    .filter(Q(receipt_no__icontains=query)
                            | Q(pk__in=receipt_pks)
                            | Q(reference__icontains=query)
                            | Q(payer_name__icontains=query)
                            | Q(invoice__student__user__first_name__icontains=query)
                            | Q(invoice__student__user__last_name__icontains=query))[:20])
            groups.append({"title": "Receipts", "items": [{
                "label": f"{p.receipt_no} - {p.invoice.student.name}",
                "meta": f"{p.get_method_display()} - {p.amount}",
                "url": reverse("receipt", args=[p.pk]),
            } for p in rows]})

        if (is_enabled("admissions", getattr(request, "institution", None))
                and (user_has_code(request.user, "admissions.manage")
                     or user_has_code(request.user, "admissions.review"))):
            reference_pks = _number_pks(
                Application.objects.all(), "reference", query)
            rows = Application.objects.filter(
                Q(reference__icontains=query)
                | Q(pk__in=reference_pks)
                | Q(first_name__icontains=query)
                | Q(last_name__icontains=query))[:20]
            groups.append({"title": "Applications", "items": [{
                "label": f"{a.reference} - {a.name}",
                "meta": a.get_status_display(),
                "url": reverse("admissions:application_detail", args=[a.pk]),
            } for a in rows]})

    groups = [g for g in groups if g["items"]]
    return render(request, "core/search.html", {
        "query": query, "groups": groups,
        "result_count": sum(len(g["items"]) for g in groups),
    })
