"""Platform-wide kill switch for AI assistance."""

from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from platform_admin.access import platform_required
from platform_admin.services import log_platform_action

from .models import AIPlatformSetting


@platform_required("platform.manage_settings")
def platform_settings(request):
    setting = AIPlatformSetting.load()
    if request.method == "POST":
        before = {"enabled": setting.enabled, "note": setting.note}
        setting.enabled = request.POST.get("enabled") == "on"
        setting.note = request.POST.get("note", "")[:255]
        setting.updated_by = request.user
        setting.save()
        log_platform_action(
            request.user,
            "platform.ai_kill_switch_changed",
            record_type="AIPlatformSetting",
            record_id=setting.pk,
            before=before,
            after={"enabled": setting.enabled, "note": setting.note},
            request=request,
        )
        state = "open" if setting.enabled else "closed"
        messages.success(request, f"The platform AI switch is now {state}.")
        return redirect("platform_admin:platform_ai_settings")
    return render(request, "ai/platform_settings.html", {"setting": setting})


@require_POST
@platform_required("platform.manage_settings")
def platform_close(request):
    """One-click close, available even if the settings form is not displayed."""
    setting = AIPlatformSetting.load()
    before = {"enabled": setting.enabled}
    setting.enabled = False
    setting.updated_by = request.user
    setting.save()
    log_platform_action(
        request.user,
        "platform.ai_kill_switch_closed",
        record_type="AIPlatformSetting",
        record_id=setting.pk,
        before=before,
        after={"enabled": False},
        request=request,
    )
    messages.success(request, "AI assistance is closed across the platform.")
    return redirect("platform_admin:platform_ai_settings")
