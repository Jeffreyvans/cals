"""Approval workflows.

A workflow template is an ordered list of steps. Each step names the role
that may act on it. A request walks through the steps; every decision is
recorded and the people who need to act are notified.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from core.audit import log_action
from core.models import (
    ApprovalRequest,
    Institution,
    Role,
    WorkflowTemplate,
)
from core.workflow import WorkflowError, approve, cancel, reject, return_for_changes


def _institution(request):
    inst = getattr(request, "institution", None)
    if inst is None:
        inst = Institution.objects.filter(slug="default").first()
    return inst


def _require(request, code):
    from core.access import user_has_code

    if not user_has_code(request.user, code):
        raise PermissionDenied("Your role does not allow this page.")


def _is_approver_anywhere(user, institution):
    """True when the user may act on some step of some workflow."""
    from core.models import WorkflowStep

    if getattr(user, "is_superuser", False):
        return True
    role_ids = set(
        WorkflowStep.objects.filter(template__institution=institution)
        .values_list("approver_role_id", flat=True)
    )
    if not role_ids:
        return False
    if user.primary_role_id in role_ids:
        return True
    return user.extra_roles.filter(pk__in=role_ids).exists()


def _require_approver(request, institution):
    """Let the workflow admins in, and the people who are named as approvers."""
    from core.access import user_has_code

    if user_has_code(request.user, "admin.workflow_approve"):
        return
    if _is_approver_anywhere(request.user, institution):
        return
    raise PermissionDenied("Your role does not allow this page.")


@login_required
def workflow_list(request):
    """The approval workflows of this institution."""
    _require(request, "admin.manage_workflows")
    inst = _institution(request)
    rows = WorkflowTemplate.objects.filter(institution=inst).prefetch_related("steps")
    for row in rows:
        row.step_count = row.steps.count()
        row.open_count = row.requests.filter(status=ApprovalRequest.Status.PENDING).count()
    return render(request, "core/workflow_list.html", {
        "inst": inst,
        "rows": rows,
        "roles": Role.objects.filter(institution=inst),
    })


@login_required
def workflow_create(request):
    """Add an approval workflow with its steps."""
    _require(request, "admin.manage_workflows")
    inst = _institution(request)
    if request.method == "POST":
        name = request.POST.get("name", "").strip()[:120]
        code = request.POST.get("code", "").strip()[:60]
        if not name or not code:
            messages.error(request, "A workflow needs a name and a code.")
        elif WorkflowTemplate.objects.filter(institution=inst, code=code).exists():
            messages.error(request, f"The code '{code}' is already used.")
        else:
            template = WorkflowTemplate.objects.create(
                institution=inst,
                name=name,
                code=code,
                description=request.POST.get("description", "")[:200],
                applies_to=request.POST.get("applies_to", "")[:60],
            )
            log_action(request.user, "workflow.created", template, request=request)
            messages.success(request, f"Workflow {template.name} added. Add its steps next.")
            return redirect("workflow_detail", pk=template.pk)
    return render(request, "core/workflow_form.html", {
        "inst": inst,
        "roles": Role.objects.filter(institution=inst),
    })


@login_required
def workflow_detail(request, pk):
    """Edit a workflow and the steps it walks through."""
    _require(request, "admin.manage_workflows")
    inst = _institution(request)
    template = get_object_or_404(WorkflowTemplate, pk=pk, institution=inst)
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "template":
            template.name = request.POST.get("name", template.name)[:120]
            template.description = request.POST.get("description", "")[:200]
            template.applies_to = request.POST.get("applies_to", "")[:60]
            template.is_active = request.POST.get("is_active") == "on"
            template.save()
            log_action(request.user, "workflow.updated", template, request=request)
            messages.success(request, "Workflow saved.")
            return redirect("workflow_detail", pk=template.pk)
        if action == "add_step":
            role = get_object_or_404(Role, pk=request.POST.get("role") or 0, institution=inst)
            order = template.steps.count() + 1
            template.steps.create(
                order=order,
                name=request.POST.get("name", "")[:120],
                approver_role=role,
                required_permission=request.POST.get("required_permission", "")[:80],
            )
            log_action(request.user, "workflow.step_added", template, request=request)
            messages.success(request, "Step added.")
            return redirect("workflow_detail", pk=template.pk)
        if action == "delete_step":
            step = template.steps.filter(pk=request.POST.get("step") or 0).first()
            if step is not None:
                log_action(request.user, "workflow.step_removed", template,
                           after={"step": step.order, "role": step.approver_role.code},
                           request=request)
                step.delete()
                messages.success(request, "Step removed.")
            return redirect("workflow_detail", pk=template.pk)
    steps = list(template.steps.select_related("approver_role"))
    for step in steps:
        step.holders = step.approver_role.primary_users.count() + step.approver_role.extra_users.count()
    return render(request, "core/workflow_form.html", {
        "inst": inst,
        "template": template,
        "steps": steps,
        "roles": Role.objects.filter(institution=inst),
    })


@login_required
def workflow_delete(request, pk):
    """Remove a workflow that has no open requests."""
    _require(request, "admin.manage_workflows")
    inst = _institution(request)
    template = get_object_or_404(WorkflowTemplate, pk=pk, institution=inst)
    if template.requests.filter(status=ApprovalRequest.Status.PENDING).exists():
        messages.error(request, "This workflow still has requests waiting. Decide them first.")
        return redirect("workflow_list")
    if request.method == "POST":
        log_action(request.user, "workflow.deleted", template, request=request)
        template.delete()
        messages.success(request, "Workflow removed.")
        return redirect("workflow_list")
    return render(request, "core/confirm_delete.html", {
        "inst": inst,
        "title": f"Remove the workflow {template.name}?",
        "detail": "The requests it already decided keep their history.",
        "cancel": reverse("workflow_list"),
    })


@login_required
def approvals(request):
    """The requests waiting for a decision, and the ones already decided."""
    inst = _institution(request)
    _require_approver(request, inst)
    status = request.GET.get("status", ApprovalRequest.Status.PENDING)
    rows = ApprovalRequest.objects.filter(institution=inst)
    if status:
        rows = rows.filter(status=status)
    else:
        rows = rows.filter(status=ApprovalRequest.Status.PENDING)
    rows = rows.select_related("template", "requester")[:200]
    mine = [r for r in rows if _can_act(r, request.user)]
    return render(request, "core/approvals.html", {
        "inst": inst,
        "rows": rows,
        "status": status,
        "mine": mine,
        "statuses": ApprovalRequest.Status.choices,
    })


def _can_act(approval, user):
    from core.workflow import can_act

    return can_act(approval, user)


@login_required
def approval_detail(request, pk):
    """One request, its steps and the decisions so far."""
    inst = _institution(request)
    _require_approver(request, inst)
    approval = get_object_or_404(ApprovalRequest, pk=pk, institution=inst)
    may_act = _can_act(approval, request.user)
    if request.method == "POST":
        decision = request.POST.get("decision", "")
        comment = request.POST.get("comment", "").strip()
        try:
            if decision == "approve":
                approve(approval, request.user, comment)
            elif decision == "reject":
                reject(approval, request.user, comment)
            elif decision == "return":
                return_for_changes(approval, request.user, comment)
            elif decision == "cancel":
                cancel(approval, request.user, comment)
            else:
                messages.error(request, "That is not a decision.")
                return redirect("approval_detail", pk=approval.pk)
        except WorkflowError as exc:
            messages.error(request, str(exc))
            return redirect("approval_detail", pk=approval.pk)
        log_action(request.user, f"approval.{decision}", approval, request=request,
                   after={"comment": comment})
        messages.success(request, f"Request {decision}d.")
        return redirect("approvals")
    steps = list(approval.template.steps.select_related("approver_role"))
    for step in steps:
        step.state = "done" if step.order < approval.current_step else (
            "current" if step.order == approval.current_step else "waiting"
        )
    return render(request, "core/approval_detail.html", {
        "inst": inst,
        "approval": approval,
        "steps": steps,
        "actions": approval.actions.select_related("actor"),
        "may_act": may_act,
    })
