from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse

from core.access import user_has_any_code
from core.audit import log_action, log_permission_denied
from core.modules import is_enabled

from .services import allocate_rooms, allocation_plan


def _guard(request):
    if not is_enabled("boarding", request.institution):
        raise Http404("This module is not switched on for your institution.")
    if not user_has_any_code(request.user, ("boarding.manage",)):
        log_permission_denied(request, ("boarding.manage",))
        raise PermissionDenied(
            "Your role does not allow this action. Ask an administrator for access."
        )


@login_required
def allocate(request):
    """Preview and apply automatic room/bed allocation for waiting boarders."""
    _guard(request)
    if request.method == "POST":
        plan = allocate_rooms()
        log_action(
            request.user,
            "boarding.rooms.allocated",
            after={"placed": len(plan["assignments"]), "waiting": len(plan["unplaced"])},
            request=request,
        )
        messages.success(
            request,
            f"{len(plan['assignments'])} boarder"
            f"{'s' if len(plan['assignments']) != 1 else ''} allocated.",
        )
        return redirect("boarding:allocate")
    return render(
        request,
        "boarding/allocate.html",
        {
            "plan": allocation_plan(),
            "hub_url": reverse("boarding:hub"),
            "list_url": reverse("boarding:list", args=["boarders"]),
        },
    )
