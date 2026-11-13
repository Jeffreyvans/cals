from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import render

from core.access import user_has_code
from core.audit import log_permission_denied, log_sensitive_view
from core.modules import is_enabled

from .models import HealthProfile


@login_required
def safe_alerts(request):
    """Only the notes a nurse has explicitly cleared for teachers."""
    if not is_enabled("health", request.institution):
        raise Http404("This module is not switched on for your institution.")
    if not user_has_code(request.user, "health.view_safe_alerts"):
        log_permission_denied(request, ("health.view_safe_alerts",))
        raise PermissionDenied(
            "Your role does not allow this action. Ask an administrator for access."
        )
    log_sensitive_view(request, "HealthProfile", "safe_alerts", request.path)
    profiles = (
        HealthProfile.objects.filter(share_with_teachers=True)
        .select_related("student__user", "student__school_class")
        .order_by("student__user__last_name", "student__user__first_name")
    )
    return render(
        request,
        "health/safe_alerts.html",
        {"profiles": profiles, "module_title": "Health and sick bay"},
    )
