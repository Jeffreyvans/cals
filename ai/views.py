"""Screens for AI consent, institution switches and usage."""

from django.contrib import messages
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from core.access import user_has_code
from core.audit import log_action
from core.permissions import permission_required_code

from .features import (
    ALL_FEATURES,
    CONSENT_TARGETS,
    CONSENT_VERSION,
    FEATURE_DISCLOSURES,
    FEATURE_LABELS,
    PROVIDER_NAME,
    RECORDED_ALWAYS,
    disclosure_for,
)
from .models import AIConsent, AIFeatureSetting, AIFeedback, AIUsageLog
from .output import sanitize_output
from .redaction import redact_text
from .safety import names_for
from .usage import feature_rows, usage_counts


def _institution(request):
    inst = getattr(request, "institution", None)
    if inst is not None:
        return inst
    return getattr(request.user, "institution", None)


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or None


def _safe_next(request):
    """Only return to a same-site page after a feedback POST."""
    target = request.POST.get("next") or request.GET.get("next") or reverse("ai:home")
    if not url_has_allowed_host_and_scheme(
        target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return reverse("ai:home")
    return target


def _valid_feature(value, default=None):
    if value in {code for code, _label in CONSENT_TARGETS}:
        return value
    return default


def _positive_int(value, default, cap=1_000_000):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(cap, parsed))


@permission_required_code("ai.use")
def home(request):
    """A small landing page linking the AI controls the person may use."""
    inst = _institution(request)
    if inst is None:
        raise Http404("No institution is linked to this account.")
    rows = feature_rows(inst)
    return render(
        request,
        "ai/home.html",
        {
            "rows": rows,
            "can_manage": user_has_code(request.user, "ai.manage"),
            "consent_version": CONSENT_VERSION,
        },
    )


@permission_required_code("ai.use")
def consent(request):
    """Show what is shared and recorded, then store a named acceptance."""
    inst = _institution(request)
    if inst is None:
        raise Http404("No institution is linked to this account.")
    feature = _valid_feature(
        request.POST.get("feature") or request.GET.get("feature"), ALL_FEATURES
    )
    if request.method == "POST":
        if request.POST.get("action") == "withdraw":
            active = AIConsent.all_objects.filter(
                institution=inst, accepted_by=request.user, withdrawn_at__isnull=True
            )
            if feature != ALL_FEATURES:
                active = active.filter(feature__in=[feature, ALL_FEATURES])
            active.update(withdrawn_at=timezone.now())
            log_action(request.user, "ai.consent_withdrawn", institution=inst,
                       after={"feature": feature})
            messages.success(request, "AI consent has been withdrawn.")
            return redirect("ai:consent")
        if request.POST.get("accept") != "yes":
            messages.error(request, "Tick the box to accept the AI disclosure.")
            return redirect("ai:consent")
        disclosure = disclosure_for(feature)
        record = AIConsent.all_objects.create(
            institution=inst,
            accepted_by=request.user,
            feature=feature,
            version=CONSENT_VERSION,
            shared_categories=disclosure["shared"],
            recorded_categories=RECORDED_ALWAYS,
            purpose=disclosure["purpose"],
            ip_address=_client_ip(request),
        )
        log_action(
            request.user, "ai.consent_accepted", instance=record,
            institution=inst, after={"feature": feature, "version": CONSENT_VERSION},
            request=request,
        )
        messages.success(request, "Thank you. Your AI consent has been recorded.")
        return redirect("ai:home")
    acceptance = (
        AIConsent.all_objects.filter(institution=inst, accepted_by=request.user)
        .order_by("-accepted_at", "-pk")
        .first()
    )
    return render(
        request,
        "ai/consent.html",
        {
            "feature": feature,
            "targets": CONSENT_TARGETS,
            "disclosure": disclosure_for(feature),
            "recorded": RECORDED_ALWAYS,
            "provider_name": PROVIDER_NAME,
            "version": CONSENT_VERSION,
            "acceptance": acceptance,
            "accepted_by": request.user,
            "feature_labels": FEATURE_LABELS,
            "feature_disclosures": FEATURE_DISCLOSURES,
        },
    )


@permission_required_code("ai.manage")
def settings_view(request):
    """Switch features on or off and set their four usage limits."""
    inst = _institution(request)
    if inst is None:
        raise Http404("No institution is linked to this account.")
    if request.method == "POST":
        feature = request.POST.get("feature", "")
        if feature not in FEATURE_LABELS:
            messages.error(request, "Choose a valid AI feature.")
            return redirect("ai:settings")
        current = AIFeatureSetting.all_objects.filter(institution=inst, feature=feature).first()
        if current is None:
            current = AIFeatureSetting(institution=inst, feature=feature)
        before = {
            "enabled": current.enabled,
            "institution_daily_limit": current.institution_daily_limit,
            "institution_monthly_limit": current.institution_monthly_limit,
            "user_daily_limit": current.user_daily_limit,
            "user_monthly_limit": current.user_monthly_limit,
        }
        if request.POST.get("action") == "toggle":
            current.enabled = request.POST.get("enabled") == "on"
        elif request.POST.get("action") == "limits":
            for field in (
                "institution_daily_limit", "institution_monthly_limit",
                "user_daily_limit", "user_monthly_limit",
            ):
                setattr(current, field, _positive_int(request.POST.get(field), getattr(current, field)))
        else:
            messages.error(request, "That AI settings action is not recognised.")
            return redirect("ai:settings")
        current.updated_by = request.user
        current.save()
        log_action(
            request.user, "ai.feature_setting_changed", instance=current,
            institution=inst, before=before,
            after={
                "feature": feature, "enabled": current.enabled,
                "institution_daily_limit": current.institution_daily_limit,
                "institution_monthly_limit": current.institution_monthly_limit,
                "user_daily_limit": current.user_daily_limit,
                "user_monthly_limit": current.user_monthly_limit,
            },
            request=request,
        )
        messages.success(request, f"{FEATURE_LABELS[feature]} settings were saved.")
        return redirect("ai:settings")
    return render(
        request,
        "ai/settings.html",
        {"rows": feature_rows(inst), "consent_version": CONSENT_VERSION},
    )


@permission_required_code("ai.use")
def usage(request):
    """Show today/month figures beside the limits, plus a redacted log."""
    inst = _institution(request)
    if inst is None:
        raise Http404("No institution is linked to this account.")
    counts = usage_counts(inst, request.user)
    can_manage = user_has_code(request.user, "ai.manage")
    logs = AIUsageLog.all_objects.filter(institution=inst)
    if not can_manage:
        logs = logs.filter(user=request.user)
    return render(
        request,
        "ai/usage.html",
        {
            "counts": counts,
            "rows": feature_rows(inst),
            "recent": list(logs.select_related("user").order_by("-created")[:50]),
            "can_manage": can_manage,
        },
    )


@permission_required_code("ai.use")
def submit_feedback(request):
    """Record a redacted bad-answer report. Nothing is sent to a provider."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    inst = _institution(request)
    if inst is None:
        raise Http404("No institution is linked to this account.")
    feature = request.POST.get("feature", "")
    if feature not in FEATURE_LABELS:
        messages.error(request, "Choose a valid AI feature.")
        return redirect(_safe_next(request))
    category = request.POST.get("category", "")
    if category not in {code for code, _label in AIFeedback.Category.choices}:
        category = AIFeedback.Category.WRONG
    comment = sanitize_output(
        redact_text(request.POST.get("comment", ""), names_for(request.user, inst)),
        limit=500,
    )
    log = None
    raw_id = str(request.POST.get("usage_log", "")).strip()
    if raw_id.isdigit():
        log = AIUsageLog.all_objects.filter(
            pk=int(raw_id), institution=inst, user=request.user
        ).first()
    row = AIFeedback.all_objects.create(
        institution=inst,
        user=request.user,
        usage_log=log,
        feature=feature,
        category=category,
        comment=comment,
    )
    log_action(
        request.user, "ai.feedback_submitted", instance=row,
        institution=inst,
        after={"feature": feature, "category": category, "usage_log_id": log.pk if log else None},
        request=request,
    )
    messages.success(request, "Thank you. Your feedback was recorded.")
    return redirect(_safe_next(request))


@permission_required_code("ai.view_usage")
def feedback_review(request):
    """Let school administrators, or platform staff, read bad-answer reports."""
    inst = _institution(request)
    if inst is None and not user_has_code(request.user, "platform.manage_settings"):
        raise Http404("No institution is linked to this account.")
    rows = AIFeedback.all_objects.select_related("user", "usage_log").order_by("-created")
    if inst is not None:
        rows = rows.filter(institution=inst)
    return render(request, "ai/feedback.html", {"rows": list(rows[:100])})
