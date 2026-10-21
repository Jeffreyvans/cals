"""Approval workflow engine.

A request walks through the steps of its template in order. Every action is
recorded, and the people who need to act are notified.
"""

from django.db import transaction
from django.utils import timezone

from core.models import ApprovalAction, ApprovalRequest, WorkflowTemplate


class WorkflowError(Exception):
    """Raised when a request cannot move the way the caller asked."""


def template_for(institution, code):
    """The active workflow template with this code, or None."""
    return WorkflowTemplate.objects.filter(
        institution=institution, code=code, is_active=True
    ).first()


@transaction.atomic
def submit(template, subject, requester, reason="", subject_label=""):
    """Send a request into its workflow at step 1."""
    if template is None:
        raise WorkflowError("This workflow is not set up for your institution.")
    if not template.steps.exists():
        raise WorkflowError("This workflow has no steps yet. Ask an administrator to add them.")
    request = ApprovalRequest.objects.create(
        institution=template.institution,
        template=template,
        requester=requester,
        subject_type=subject.__class__.__name__,
        subject_id=str(subject.pk),
        subject_label=subject_label or str(subject)[:200],
        reason=reason,
        current_step=template.steps.order_by("order").first().order,
    )
    ApprovalAction.objects.create(
        request=request, actor=requester, decision=ApprovalAction.Decision.SUBMITTED, comment=reason
    )
    notify_step(request)
    return request


def _approvers_for(step, institution):
    """Users who may act on this step: holders of the role or the permission."""
    from accounts.models import User
    from core.access import user_has_code

    if not step:
        return User.objects.none()
    users = User.objects.filter(
        institution=institution, is_active=True, extra_roles=step.approver_role
    ).distinct()
    if step.required_permission:
        extra = [
            u
            for u in User.objects.filter(institution=institution, is_active=True)
            if user_has_code(u, step.required_permission)
        ]
        if extra:
            users = list(users) + extra
    return users


def notify_step(request):
    """Tell the people who can act on the current step."""
    from core.notifications import notify_users

    step = request.current_step_object()
    users = _approvers_for(step, request.institution)
    if not users:
        return 0
    return notify_users(
        users,
        title=f"Approval needed: {request.subject_label or request.subject_type}",
        body=request.reason or "A request is waiting for your approval.",
        link=request.get_absolute_url() if hasattr(request, "get_absolute_url") else "",
        category="workflow",
        institution=request.institution,
    )


def can_act(request, user):
    """True when this user may decide the current step."""
    from core.access import user_has_code

    step = request.current_step_object()
    if not step or not request.is_open:
        return False
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "institution_id", None) != request.institution_id:
        return False
    if getattr(user, "is_superuser", False):
        return True
    if user_has_code(user, "admin.workflow_approve"):
        return True
    if user.extra_roles.filter(pk=step.approver_role_id).exists():
        return True
    if user.primary_role_id == step.approver_role_id:
        return True
    if step.required_permission and user_has_code(user, step.required_permission):
        return True
    return False


def _finish(request, status, actor, comment):
    request.status = status
    request.decided_at = timezone.now()
    request.save(update_fields=["status", "decided_at"])


@transaction.atomic
def approve(request, actor, comment=""):
    """Approve the current step and move to the next one, or finish."""
    if not request.is_open:
        raise WorkflowError("This request has already been decided.")
    if not can_act(request, actor):
        raise WorkflowError("You are not the approver for this step.")
    step = request.current_step_object()
    ApprovalAction.objects.create(
        request=request, actor=actor, decision=ApprovalAction.Decision.APPROVED, comment=comment
    )
    next_step = request.template.steps.filter(order__gt=step.order).order_by("order").first()
    if next_step is None:
        _finish(request, ApprovalRequest.Status.APPROVED, actor, comment)
        from core.notifications import notify_users

        notify_users(
            [request.requester] if request.requester_id else [],
            title=f"Approved: {request.subject_label}",
            body=comment or "Your request was approved.",
            category="workflow",
            institution=request.institution,
        )
    else:
        request.current_step = next_step.order
        request.save(update_fields=["current_step"])
        notify_step(request)
    return request


@transaction.atomic
def reject(request, actor, comment=""):
    """Reject the request. The reason should say why."""
    if not request.is_open:
        raise WorkflowError("This request has already been decided.")
    if not can_act(request, actor):
        raise WorkflowError("You are not the approver for this step.")
    ApprovalAction.objects.create(
        request=request, actor=actor, decision=ApprovalAction.Decision.REJECTED, comment=comment
    )
    _finish(request, ApprovalRequest.Status.REJECTED, actor, comment)
    from core.notifications import notify_users

    notify_users(
        [request.requester] if request.requester_id else [],
        title=f"Rejected: {request.subject_label}",
        body=comment or "Your request was rejected.",
        category="workflow",
        institution=request.institution,
    )
    return request


@transaction.atomic
def return_for_changes(request, actor, comment=""):
    """Send the request back to the requester for changes."""
    if not request.is_open:
        raise WorkflowError("This request has already been decided.")
    if not can_act(request, actor):
        raise WorkflowError("You are not the approver for this step.")
    ApprovalAction.objects.create(
        request=request, actor=actor, decision=ApprovalAction.Decision.RETURNED, comment=comment
    )
    _finish(request, ApprovalRequest.Status.RETURNED, actor, comment)
    from core.notifications import notify_users

    notify_users(
        [request.requester] if request.requester_id else [],
        title=f"Changes needed: {request.subject_label}",
        body=comment or "Please make the changes and send the request again.",
        category="workflow",
        institution=request.institution,
    )
    return request


@transaction.atomic
def cancel(request, actor, comment=""):
    """Withdraw a request that has not been decided yet."""
    if not request.is_open:
        raise WorkflowError("This request has already been decided.")
    ApprovalAction.objects.create(
        request=request, actor=actor, decision=ApprovalAction.Decision.CANCELLED, comment=comment
    )
    _finish(request, ApprovalRequest.Status.CANCELLED, actor, comment)
    return request
