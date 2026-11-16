"""Reusable registry-driven screens for Phase 7 department modules.

Each department app defines the models it needs and a small registry of the
registers it wants to show. The views here enforce the module switch and the
module's permission codes before they touch a row, write audit entries for
changes, and expose read-only JSON summaries for integrations.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse

from core.access import user_has_any_code
from core.audit import log_action, log_permission_denied, log_sensitive_view
from core.modules import is_enabled


def _flag(module_code, module):
    return module.get("flag", module_code)


def _guard(request, module_code, registry, permission=None):
    module = registry.get(module_code)
    if module is None:
        raise Http404("There is no such module.")
    if not is_enabled(_flag(module_code, module), request.institution):
        raise Http404("This module is not switched on for your institution.")
    codes = permission or module["permissions"]
    codes = (codes,) if isinstance(codes, str) else tuple(codes)
    if not user_has_any_code(request.user, codes):
        log_permission_denied(request, codes)
        raise PermissionDenied(
            "Your role does not allow this action. Ask an administrator for access."
        )
    return module


def _write_codes(module):
    """A read-only role may browse a register but only a manager may change it."""
    codes = tuple(module["permissions"])
    manage = tuple(code for code in codes if code.endswith(".manage"))
    return manage or codes


def _kind(module, kind_slug):
    for kind in module["kinds"]:
        if kind["slug"] == kind_slug:
            return kind
    raise Http404("There is no such register.")


def _objects(request, kind):
    """Rows this user may touch for one register (author-only rows, for example)."""
    scope = kind.get("queryset")
    if scope is not None:
        return scope(request)
    return kind["model"].objects.all()


def _search(queryset, query, fields):
    if not query:
        return queryset
    condition = Q()
    for field in fields:
        condition |= Q(**{f"{field}__icontains": query})
    return queryset.filter(condition)


def _json_value(value):
    """Make a column value JSON-safe without losing the model's label."""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


def _display(obj, name):
    getter = getattr(obj, f"get_{name}_display", None)
    if getter is not None:
        try:
            return getter()
        except AttributeError:
            pass
    return getattr(obj, name, "")


def _fields(obj, kind):
    names = kind.get("detail_fields")
    if names:
        return [(name.replace("_", " ").title(), _display(obj, name)) for name in names]
    rows = []
    for field in obj._meta.fields:
        if field.name in {"institution", "created", "updated"}:
            continue
        rows.append((field.verbose_name.title(), _display(obj, field.name)))
    return rows


def _set_actor(obj, user):
    for field in (
        "created_by",
        "uploaded_by",
        "requested_by",
        "reported_by",
        "issued_by",
        "received_by",
        "opened_by",
        "author",
        "counsellor",
        "referred_by",
    ):
        if hasattr(obj, f"{field}_id") and getattr(obj, f"{field}_id") is None:
            setattr(obj, field, user)


def build_urls(module_code, registry, namespace, hub_path=""):
    """Build the URL patterns for one registry-driven department module.

    ``hub_path`` lets an app whose mount prefix is already used by another
    view (the health app shares ``/health/`` with the deploy health check)
    move its hub without moving the rest of its screens.
    """

    def guard(request, permission=None):
        return _guard(request, module_code, registry, permission)

    def hub(request):
        module = guard(request)
        kinds = []
        for kind in module["kinds"]:
            kinds.append(
                {
                    **kind,
                    "count": _objects(request, kind).count(),
                    "url": reverse(f"{namespace}:list", args=[kind["slug"]]),
                }
            )
        return render(
            request,
            "registers/hub.html",
            {
                "module": module,
                "module_code": module_code,
                "kinds": kinds,
                "links": [
                    {"title": title, "url": reverse(name)}
                    for title, name in module.get("links", [])
                ],
                "api_url": reverse(f"{namespace}:api_summary"),
            },
        )

    def record_list(request, kind_slug):
        module = guard(request)
        kind = _kind(module, kind_slug)
        if kind.get("sensitive"):
            log_sensitive_view(request, kind["model"].__name__, "list", request.path)
        query = (request.GET.get("q") or "").strip()
        queryset = _objects(request, kind)
        queryset = _search(queryset, query, kind.get("search", []))
        total = queryset.count()
        rows = []
        for obj in queryset[:300]:
            action_url = ""
            action_label = ""
            if kind.get("row_action"):
                name, action_label = kind["row_action"]
                action_url = reverse(name, args=[obj.pk])
            rows.append(
                {
                    "object": obj,
                    "cells": [column[1](obj) for column in kind["columns"]],
                    "detail_url": reverse(
                        f"{namespace}:detail", args=[kind_slug, obj.pk]
                    ),
                    "edit_url": reverse(f"{namespace}:edit", args=[kind_slug, obj.pk]),
                    "delete_url": reverse(
                        f"{namespace}:delete", args=[kind_slug, obj.pk]
                    ),
                    "action_url": action_url,
                    "action_label": action_label,
                }
            )
        return render(
            request,
            "registers/list.html",
            {
                "module": module,
                "kind": kind,
                "rows": rows,
                "columns": [column[0] for column in kind["columns"]],
                "query": query,
                "total": total,
                "can_create": not kind.get("no_create", False),
                "create_url": reverse(f"{namespace}:create", args=[kind_slug]),
                "hub_url": reverse(f"{namespace}:hub"),
                "api_url": reverse(f"{namespace}:api_records", args=[kind_slug]),
            },
        )

    def record_detail(request, kind_slug, pk):
        module = guard(request)
        kind = _kind(module, kind_slug)
        obj = get_object_or_404(_objects(request, kind), pk=pk)
        if kind.get("sensitive"):
            log_sensitive_view(request, kind["model"].__name__, obj.pk, request.path)
        fields = _fields(obj, kind)
        can_edit = not kind.get("no_edit", False)
        return render(
            request,
            "registers/detail.html",
            {
                "module": module,
                "kind": kind,
                "object": obj,
                "fields": fields,
                "can_edit": can_edit,
                "edit_url": reverse(f"{namespace}:edit", args=[kind_slug, obj.pk])
                if can_edit
                else "",
                "delete_url": reverse(f"{namespace}:delete", args=[kind_slug, obj.pk]),
                "list_url": reverse(f"{namespace}:list", args=[kind_slug]),
            },
        )

    def record_create(request, kind_slug):
        module = guard(request, _write_codes(registry[module_code]))
        kind = _kind(module, kind_slug)
        if kind.get("no_create"):
            raise Http404("This register is filled in from another screen.")
        form = kind["form"](request.POST or None, request.FILES or None)
        if request.method == "POST" and form.is_valid():
            obj = form.save(commit=False)
            _set_actor(obj, request.user)
            obj.save()
            form.save_m2m()
            log_action(
                request.user,
                f"{module_code}.{kind_slug}.created",
                instance=obj,
                after={"label": str(obj)},
                request=request,
            )
            messages.success(request, f"{kind['title']} saved.")
            return redirect(f"{namespace}:list", kind_slug=kind_slug)
        return render(
            request,
            "registers/form.html",
            {
                "module": module,
                "kind": kind,
                "form": form,
                "instance": None,
                "cancel_url": reverse(f"{namespace}:list", args=[kind_slug]),
            },
        )

    def record_edit(request, kind_slug, pk):
        module = guard(request, _write_codes(registry[module_code]))
        kind = _kind(module, kind_slug)
        if kind.get("no_edit"):
            raise Http404("This register is edited from another screen.")
        instance = get_object_or_404(_objects(request, kind), pk=pk)
        form = kind["form"](
            request.POST or None, request.FILES or None, instance=instance
        )
        if request.method == "POST" and form.is_valid():
            obj = form.save()
            log_action(
                request.user,
                f"{module_code}.{kind_slug}.updated",
                instance=obj,
                after={"label": str(obj)},
                request=request,
            )
            messages.success(request, f"{kind['title']} saved.")
            return redirect(f"{namespace}:list", kind_slug=kind_slug)
        return render(
            request,
            "registers/form.html",
            {
                "module": module,
                "kind": kind,
                "form": form,
                "instance": instance,
                "cancel_url": reverse(f"{namespace}:list", args=[kind_slug]),
            },
        )

    def record_delete(request, kind_slug, pk):
        module = guard(request, _write_codes(registry[module_code]))
        kind = _kind(module, kind_slug)
        instance = get_object_or_404(_objects(request, kind), pk=pk)
        if request.method == "POST":
            try:
                instance.delete()
                log_action(
                    request.user,
                    f"{module_code}.{kind_slug}.deleted",
                    model_name=kind["model"].__name__,
                    object_id=str(pk),
                    request=request,
                )
                messages.success(request, "Record removed.")
            except ProtectedError:
                messages.error(request, "This record is still used elsewhere.")
            return redirect(f"{namespace}:list", kind_slug=kind_slug)
        return render(
            request,
            "registers/confirm_delete.html",
            {
                "module": module,
                "kind": kind,
                "object": instance,
                "list_url": reverse(f"{namespace}:list", args=[kind_slug]),
                "delete_url": reverse(f"{namespace}:delete", args=[kind_slug, pk]),
            },
        )

    def api_summary(request):
        module = guard(request)
        return JsonResponse(
            {
                "module": module_code,
                "title": module["title"],
                "kinds": [
                    {
                        "slug": kind["slug"],
                        "title": kind["title"],
                        "count": _objects(request, kind).count(),
                    }
                    for kind in module["kinds"]
                ],
            }
        )

    def api_records(request, kind_slug):
        guard(request)
        kind = _kind(registry[module_code], kind_slug)
        if kind.get("sensitive"):
            log_sensitive_view(request, kind["model"].__name__, "api", request.path)
        query = (request.GET.get("q") or "").strip()
        queryset = _search(
            _objects(request, kind), query, kind.get("search", [])
        )
        limit = min(int(request.GET.get("limit", 50) or 50), 200)
        results = [
            {
                "id": obj.pk,
                "label": str(obj),
                "fields": {
                    label: _json_value(fn(obj)) for label, fn in kind["columns"]
                },
            }
            for obj in queryset[:limit]
        ]
        return JsonResponse({"kind": kind_slug, "count": queryset.count(), "results": results})

    def login(view):
        return login_required(view)

    patterns = [
        path("api/summary.json", login(api_summary), name="api_summary"),
        path("api/<slug:kind_slug>/", login(api_records), name="api_records"),
        path(hub_path, login(hub), name="hub"),
        path("<slug:kind_slug>/", login(record_list), name="list"),
        path("<slug:kind_slug>/new/", login(record_create), name="create"),
        path("<slug:kind_slug>/<int:pk>/", login(record_detail), name="detail"),
        path("<slug:kind_slug>/<int:pk>/edit/", login(record_edit), name="edit"),
        path("<slug:kind_slug>/<int:pk>/delete/", login(record_delete), name="delete"),
    ]
    return patterns
