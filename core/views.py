"""Screens for the foundation: settings, roles, notifications and the audit log."""

import csv

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
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

PERM_CATEGORIES = []
for _code, _desc, _cat in PERMISSIONS:
    if _cat not in PERM_CATEGORIES:
        PERM_CATEGORIES.append(_cat)


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
            inst.currency = request.POST.get("currency", inst.currency)[:10]
            inst.timezone = request.POST.get("timezone", inst.timezone)[:64]
            inst.language = request.POST.get("language", inst.language)[:10]
            inst.address = request.POST.get("address", "")[:2000]
            inst.contact_email = request.POST.get("contact_email", "")[:200]
            inst.contact_phone = request.POST.get("contact_phone", "")[:30]
            inst.primary_color = request.POST.get("primary_color", inst.primary_color)[:20]
            inst.accent_color = request.POST.get("accent_color", inst.accent_color)[:20]
            inst.save()
            log_action(request.user, "institution.updated", inst, request=request)
            messages.success(request, "Institution details saved.")
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
    switches = []
    for code in ALL_MODULES:
        switches.append({"code": code, "on": is_enabled(code, inst)})
    return render(request, "core/institution_settings.html", {
        "inst": inst,
        "switches": switches,
        "modules": ALL_MODULES,
    })


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
