"""Generic registers plus the payroll, leave and newsletter actions."""

import csv

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from core.access import user_has_any_code, user_has_code
from core.audit import log_action, log_permission_denied
from core.modules import is_enabled

from .documents import build_payslip_pdf
from .forms import LeaveDecisionForm
from .models import (
    LeaveRequest,
    Newsletter,
    PayrollRun,
    Payslip,
    Policy,
    Quotation,
    Requisition,
    SupplierInvoice,
)
from .registry import MODULES, get_kind, get_module
from .services import (
    approve_payroll_run,
    award_quotation,
    decide_leave,
    generate_payslips,
    match_supplier_invoice,
    post_payroll_run,
    send_newsletter,
    supplier_invoice_expected_total,
)


def _guard(request, module_code, permission=None):
    module = get_module(module_code)
    if module is None:
        raise Http404("There is no such module.")
    flag = module.get("flag", module_code)
    if not is_enabled(flag, request.institution):
        raise Http404("This module is not switched on for your institution.")
    if permission is None:
        permission = module["permissions"]
    codes = tuple(permission) if isinstance(permission, (list, tuple)) else (permission,)
    if not user_has_any_code(request.user, codes):
        log_permission_denied(request, codes)
        raise PermissionDenied(
            "Your role does not allow this action. Ask an administrator for access."
        )
    return module


def _require(request, code):
    if not user_has_code(request.user, code):
        log_permission_denied(request, (code,))
        raise PermissionDenied(
            "Your role does not allow this action. Ask an administrator for access."
        )


def _module_or_404(request, module_code):
    return _guard(request, module_code)


@login_required
def hub(request):
    """The office home page: the administration modules this user can open."""
    modules = []
    for code, info in MODULES.items():
        if not is_enabled(info.get("flag", code), request.institution):
            continue
        if not user_has_any_code(request.user, info["permissions"]):
            continue
        modules.append(
            {
                "code": code,
                "title": info["title"],
                "description": info["description"],
                "url": reverse("operations:module", args=[code]),
            }
        )
    return render(request, "operations/hub.html", {"modules": modules})


@login_required
def module_hub(request, module_code):
    module = _guard(request, module_code)
    kinds = []
    for kind in module["kinds"]:
        kinds.append(
            {
                **kind,
                "url": reverse("operations:list", args=[module_code, kind["slug"]]),
            }
        )
    return render(
        request,
        "operations/module.html",
        {
            "module": module,
            "kinds": kinds,
        },
    )


def _search(queryset, query, fields):
    if not query:
        return queryset
    condition = Q()
    for field in fields:
        condition |= Q(**{f"{field}__icontains": query})
    return queryset.filter(condition)


def _row(kind, module_code, obj):
    cells = [column[1](obj) for column in kind["columns"]]
    action_url = ""
    action_label = ""
    if kind.get("row_action"):
        name, action_label = kind["row_action"]
        action_url = reverse(name, args=[obj.pk])
    elif kind.get("detail"):
        action_url = reverse(kind["detail"], args=[obj.pk])
        action_label = kind.get("action_label", "Open")
    return {
        "object": obj,
        "cells": cells,
        "action_url": action_url,
        "action_label": action_label,
        "edit_url": reverse("operations:edit", args=[module_code, kind["slug"], obj.pk]),
        "delete_url": reverse("operations:delete", args=[module_code, kind["slug"], obj.pk]),
    }


@login_required
def record_list(request, module_code, kind_slug):
    module = _guard(request, module_code)
    kind = get_kind(module_code, kind_slug)
    if kind is None:
        raise Http404("There is no such register.")
    query = (request.GET.get("q") or "").strip()
    queryset = kind["model"].objects.all()
    queryset = _search(queryset, query, kind.get("search", []))
    total = queryset.count()
    rows = [_row(kind, module_code, obj) for obj in queryset[:300]]
    return render(
        request,
        "operations/list.html",
        {
            "module": module,
            "kind": kind,
            "rows": rows,
            "columns": [column[0] for column in kind["columns"]],
            "query": query,
            "total": total,
            "can_create": not kind.get("no_create", False),
        },
    )


def _set_actor(obj, user):
    for field in (
        "created_by",
        "uploaded_by",
        "requested_by",
        "reported_by",
        "issued_by",
        "received_by",
    ):
        if hasattr(obj, f"{field}_id") and getattr(obj, f"{field}_id") is None:
            setattr(obj, field, user)


def _form_page(request, module, kind, form, instance=None):
    return render(
        request,
        "operations/form.html",
        {
            "module": module,
            "kind": kind,
            "form": form,
            "instance": instance,
            "cancel_url": reverse("operations:list", args=[module["code"], kind["slug"]]),
        },
    )


@login_required
def record_create(request, module_code, kind_slug):
    module = _guard(request, module_code)
    kind = get_kind(module_code, kind_slug)
    if kind is None:
        raise Http404("There is no such register.")
    if kind.get("no_create"):
        raise Http404("This register is filled in from another screen.")
    form = kind["form"](request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        _set_actor(obj, request.user)
        obj.save()
        log_action(
            request.user,
            f"{module_code}.{kind_slug}.created",
            instance=obj,
            after={"label": str(obj)},
            request=request,
        )
        messages.success(request, f"{kind['title']} saved.")
        return redirect("operations:list", module_code=module_code, kind_slug=kind_slug)
    return _form_page(request, module, kind, form)


@login_required
def record_edit(request, module_code, kind_slug, pk):
    module = _guard(request, module_code)
    kind = get_kind(module_code, kind_slug)
    if kind is None:
        raise Http404("There is no such register.")
    instance = get_object_or_404(kind["model"].objects.all(), pk=pk)
    form = kind["form"](request.POST or None, request.FILES or None, instance=instance)
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
        return redirect("operations:list", module_code=module_code, kind_slug=kind_slug)
    return _form_page(request, module, kind, form, instance=instance)


@login_required
def record_delete(request, module_code, kind_slug, pk):
    _guard(request, module_code)
    kind = get_kind(module_code, kind_slug)
    if kind is None:
        raise Http404("There is no such register.")
    instance = get_object_or_404(kind["model"].objects.all(), pk=pk)
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
        return redirect("operations:list", module_code=module_code, kind_slug=kind_slug)
    return render(
        request,
        "operations/confirm_delete.html",
        {
            "module": get_module(module_code),
            "kind": kind,
            "object": instance,
            "cancel_url": reverse("operations:list", args=[module_code, kind_slug]),
            "delete_url": reverse("operations:delete", args=[module_code, kind_slug, pk]),
        },
    )


# ---------- HR ----------


@login_required
def leave_decision(request, pk):
    module = _guard(request, "hr", "hr.approve_leave")
    leave = get_object_or_404(LeaveRequest.objects.all(), pk=pk)
    form = LeaveDecisionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            decide_leave(
                leave,
                request.user,
                form.cleaned_data["decision"] == "approve",
                form.cleaned_data.get("note", ""),
            )
            messages.success(request, "Leave request updated.")
            return redirect("operations:list", module_code="hr", kind_slug="leave")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
    return render(
        request,
        "operations/decision.html",
        {
            "module": module,
            "title": f"Decide leave for {leave.staff}",
            "object": leave,
            "form": form,
            "cancel_url": reverse("operations:list", args=["hr", "leave"]),
        },
    )


# ---------- payroll ----------


@login_required
def payroll_run_detail(request, pk):
    module = _guard(request, "payroll", ("payroll.manage", "payroll.approve"))
    run = get_object_or_404(PayrollRun.objects.all(), pk=pk)
    return render(
        request,
        "operations/payroll_run.html",
        {
            "module": module,
            "run": run,
            "payslips": run.payslips.select_related("staff__user"),
            "list_url": reverse("operations:list", args=["payroll", "runs"]),
            "generate_url": reverse("operations:payroll_run_generate", args=[run.pk]),
            "approve_url": reverse("operations:payroll_run_approve", args=[run.pk]),
            "post_url": reverse("operations:payroll_run_post", args=[run.pk]),
            "bank_url": reverse("operations:payroll_run_bank", args=[run.pk]),
        },
    )


@login_required
def payroll_run_generate(request, pk):
    _guard(request, "payroll", "payroll.manage")
    run = get_object_or_404(PayrollRun.objects.all(), pk=pk)
    if request.method == "POST":
        if run.status != PayrollRun.Status.DRAFT:
            messages.error(request, "Payslips can only be generated for a draft run.")
        else:
            made = generate_payslips(run)
            messages.success(request, f"{made} payslip(s) generated.")
    return redirect("operations:payroll_run_detail", pk=run.pk)


@login_required
def payroll_run_approve(request, pk):
    _guard(request, "payroll", "payroll.approve")
    run = get_object_or_404(PayrollRun.objects.all(), pk=pk)
    if request.method == "POST":
        try:
            approve_payroll_run(run, request.user)
            messages.success(request, "Payroll run approved.")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
    return redirect("operations:payroll_run_detail", pk=run.pk)


@login_required
def payroll_run_post(request, pk):
    _guard(request, "payroll", "payroll.manage")
    run = get_object_or_404(PayrollRun.objects.all(), pk=pk)
    if request.method == "POST":
        try:
            entry = post_payroll_run(run, request.user)
            messages.success(
                request, f"Posted to the ledger as {entry.number if entry else 'payroll'}."
            )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
    return redirect("operations:payroll_run_detail", pk=run.pk)


@login_required
def payroll_run_bank(request, pk):
    _guard(request, "payroll", ("payroll.manage", "payroll.approve"))
    run = get_object_or_404(PayrollRun.objects.all(), pk=pk)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{run.reference}-bank.csv"'
    writer = csv.writer(response)
    writer.writerow(["reference", "staff", "bank", "account", "amount"])
    for slip in run.payslips.select_related("staff__user"):
        structure = slip.staff.salary_structures.first()
        writer.writerow(
            [
                slip.staff.employee_no or slip.staff.pk,
                slip.staff.user.get_full_name() or slip.staff.user.username,
                structure.bank_name if structure else "",
                structure.bank_account if structure else "",
                slip.net,
            ]
        )
    return response


@login_required
def payroll_payslip_pdf(request, pk):
    _guard(request, "payroll", ("payroll.manage", "payroll.approve"))
    payslip = get_object_or_404(
        Payslip.objects.select_related("institution", "run", "staff__user"), pk=pk
    )
    response = HttpResponse(build_payslip_pdf(payslip), content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="{payslip.run.reference}-payslip-{payslip.staff_id}.pdf"'
    )
    return response


# ---------- compliance, marketing ----------


@login_required
def policy_acknowledge(request, pk):
    _guard(request, "compliance", "compliance.manage")
    policy = get_object_or_404(Policy.objects.all(), pk=pk)
    if request.method == "POST":
        acknowledgement, created = policy.acknowledgements.get_or_create(
            institution=policy.institution,
            user=request.user,
            defaults={"acknowledged_at": timezone.now()},
        )
        if created:
            messages.success(request, "Policy acknowledged.")
        else:
            messages.info(request, "You have already acknowledged this policy.")
        return redirect("operations:list", module_code="compliance", kind_slug="policies")
    return render(
        request,
        "operations/confirm_action.html",
        {
            "module": get_module("compliance"),
            "title": f"Acknowledge {policy.title}",
            "message": "This records that you have read and accepted this policy.",
            "object": policy,
            "confirm_label": "Acknowledge",
            "cancel_url": reverse("operations:list", args=["compliance", "policies"]),
        },
    )


@login_required
def newsletter_send(request, pk):
    module = _guard(request, "marketing", "marketing.manage")
    newsletter = get_object_or_404(Newsletter.objects.all(), pk=pk)
    if request.method == "POST":
        try:
            count = send_newsletter(newsletter, request.user)
            messages.success(request, f"Newsletter queued for {count} recipient(s).")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect("operations:list", module_code="marketing", kind_slug="newsletters")
    return render(
        request,
        "operations/confirm_action.html",
        {
            "module": module,
            "title": f"Send {newsletter.title}",
            "message": "The newsletter will be queued for every active student and staff email.",
            "object": newsletter,
            "confirm_label": "Send newsletter",
            "cancel_url": reverse("operations:list", args=["marketing", "newsletters"]),
        },
    )


# ---------- procurement follow-up ----------


@login_required
def requisition_detail(request, pk):
    module = _guard(request, "procurement")
    requisition = get_object_or_404(
        Requisition.objects.select_related("department", "requested_by"), pk=pk
    )
    quotations = requisition.quotations.select_related("supplier", "awarded_by").prefetch_related(
        "lines"
    )
    can_manage = user_has_code(request.user, "procurement.manage")
    return render(
        request,
        "operations/requisition_detail.html",
        {
            "module": module,
            "requisition": requisition,
            "quotations": quotations,
            "can_manage": can_manage,
            "list_url": reverse("operations:list", args=["procurement", "requisitions"]),
            "quotation_list_url": reverse(
                "operations:list", args=["procurement", "quotations"]
            ),
            "award_url_name": "operations:quotation_award",
        },
    )


@login_required
def quotation_award(request, pk):
    _guard(request, "procurement", "procurement.manage")
    quotation = get_object_or_404(
        Quotation.objects.select_related("requisition", "supplier"), pk=pk
    )
    if request.method != "POST":
        return redirect("operations:requisition_detail", pk=quotation.requisition_id)
    try:
        order = award_quotation(quotation, request.user)
        messages.success(
            request, f"{quotation.supplier} awarded; purchase order {order.number} raised."
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("operations:requisition_detail", pk=quotation.requisition_id)


@login_required
def supplier_invoice_match(request, pk):
    module = _guard(request, "procurement")
    invoice = get_object_or_404(
        SupplierInvoice.objects.select_related("supplier", "order", "receipt"), pk=pk
    )
    if request.method == "POST":
        _guard(request, "procurement", "procurement.manage")
        try:
            match_supplier_invoice(invoice, request.user)
            if invoice.status == SupplierInvoice.Status.DISPUTED:
                messages.warning(request, invoice.match_note)
            else:
                messages.success(request, "Invoice matched and expensed.")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect(
            "operations:list", module_code="procurement", kind_slug="supplier-invoices"
        )
    expected = supplier_invoice_expected_total(invoice)
    return render(
        request,
        "operations/supplier_invoice_match.html",
        {
            "module": module,
            "invoice": invoice,
            "expected": expected,
            "variance": invoice.amount - expected,
            "can_manage": user_has_code(request.user, "procurement.manage"),
            "cancel_url": reverse(
                "operations:list", args=["procurement", "supplier-invoices"]
            ),
        },
    )
