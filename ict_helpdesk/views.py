from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from core.access import user_has_any_code
from core.audit import log_permission_denied
from core.modules import is_enabled

from .models import AccountResetRequest
from .services import complete_account_reset, reject_account_reset


def _login_link_for(user):
    institution = getattr(user, "institution", None)
    if institution is not None and getattr(institution, "slug", ""):
        return reverse("institution_login", args=[institution.slug])
    return reverse("login")


def _guard(request):
    if not is_enabled("ict_helpdesk", request.institution):
        raise Http404("This module is not switched on for your institution.")
    if not user_has_any_code(request.user, ("ict.manage",)):
        log_permission_denied(request, ("ict.manage",))
        raise PermissionDenied(
            "Your role does not allow this action. Ask an administrator for access."
        )


@login_required
def reset_manage(request, pk):
    """Decide one account-reset request.

    Completing the reset sets a new temporary password. It is shown once on
    this screen and is never stored, so it cannot leak from a later report.
    """
    _guard(request)
    reset = get_object_or_404(
        AccountResetRequest.objects.select_related("user", "requested_by"), pk=pk
    )
    temporary_password = ""
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "complete":
                reset, temporary_password = complete_account_reset(
                    reset, actor=request.user
                )
                messages.success(
                    request,
                    "The password was reset. Give the temporary password below to "
                    "the user now; it is not shown again.",
                )
            elif action == "reject":
                reset = reject_account_reset(reset, actor=request.user)
                messages.success(request, "The reset request was rejected.")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
    return render(
        request,
        "ict_helpdesk/reset_manage.html",
        {
            "reset": reset,
            "temporary_password": temporary_password,
            "login_link": _login_link_for(reset.user),
            "list_url": reverse("ict_helpdesk:list", args=["account-resets"]),
            "manage_url": reverse("ict_helpdesk:reset_manage", args=[reset.pk]),
        },
    )
