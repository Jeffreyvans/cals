"""Screens for issuing API keys and configuring webhooks.

Both are administrator tools: they sit behind ``admin.manage_settings`` and
belong to the signed-in user's institution.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.access import user_has_code
from core.audit import log_action
from core.models import Institution
from core.permissions import PERMISSIONS

from .events import EVENT_NAMES, EVENTS
from .models import ApiKey, WebhookDelivery, WebhookEndpoint
from .webhooks import deliver, dispatch_event


def _require(request):
    if not user_has_code(request.user, "admin.manage_settings"):
        raise PermissionDenied("Your role does not allow this page.")


def _institution(request):
    return getattr(request, "institution", None) or Institution.objects.filter(
        slug="default"
    ).first()


def _staff_users(institution):
    from accounts.models import User

    return (
        User.objects.filter(institution_id=institution.pk, is_active=True)
        .exclude(role__in=[User.Role.STUDENT, User.Role.PARENT])
        .order_by("last_name", "first_name", "username")
    )


@login_required
def api_keys(request):
    _require(request)
    institution = _institution(request)
    raw_key = None
    if request.method == "POST":
        action = request.POST.get("action", "create")
        if action == "create":
            user_id = request.POST.get("user") or request.user.pk
            user = _staff_users(institution).filter(pk=user_id).first()
            if user is None:
                messages.error(request, "Choose an active staff account for the key.")
            else:
                scopes = [code for code in request.POST.getlist("scopes") if code]
                key, raw_key = ApiKey.issue(
                    institution, user, request.POST.get("name", ""), scopes
                )
                log_action(
                    request.user,
                    "api.key_created",
                    key,
                    after={"user": user.username, "scopes": scopes},
                    request=request,
                )
                messages.success(
                    request, "Key created. Copy it now; it is not shown again."
                )
        elif action == "revoke":
            key = get_object_or_404(
                ApiKey, pk=request.POST.get("key"), institution=institution
            )
            key.is_active = False
            key.save(update_fields=["is_active"])
            log_action(request.user, "api.key_revoked", key, request=request)
            messages.success(request, "Key revoked.")
            return redirect("api_keys")

    keys = ApiKey.objects.filter(institution=institution).select_related("user")
    categories = []
    for code, description, category in PERMISSIONS:
        if category not in [c[0] for c in categories]:
            categories.append((category, []))
        for entry in categories:
            if entry[0] == category:
                entry[1].append((code, description))
    return render(
        request,
        "api/keys.html",
        {
            "keys": keys,
            "raw_key": raw_key,
            "staff": _staff_users(institution),
            "categories": categories,
            "selected_scopes": [],
        },
    )


@login_required
def webhooks(request):
    _require(request)
    institution = _institution(request)
    if request.method == "POST":
        name = (request.POST.get("name") or "Webhook").strip()[:100]
        url = (request.POST.get("url") or "").strip()
        events = [event for event in request.POST.getlist("events") if event in EVENT_NAMES]
        if not url.lower().startswith(("http://", "https://")):
            messages.error(request, "The URL must start with http:// or https://.")
        else:
            endpoint = WebhookEndpoint.objects.create(
                institution=institution,
                name=name,
                url=url[:500],
                secret=WebhookEndpoint.generate_secret(),
                events=events,
                created_by=request.user,
            )
            log_action(
                request.user,
                "api.webhook_created",
                endpoint,
                after={"url": endpoint.url, "events": events},
                request=request,
            )
            messages.success(request, "Webhook endpoint added.")
            return redirect("api_webhook_detail", pk=endpoint.pk)

    endpoints = WebhookEndpoint.objects.filter(institution=institution)
    return render(
        request,
        "api/webhooks.html",
        {"endpoints": endpoints, "events": EVENTS, "event_names": EVENT_NAMES},
    )


@login_required
def webhook_detail(request, pk):
    _require(request)
    institution = _institution(request)
    endpoint = get_object_or_404(WebhookEndpoint, pk=pk, institution=institution)
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "toggle":
            endpoint.is_active = not endpoint.is_active
            endpoint.save(update_fields=["is_active"])
            messages.success(
                request, f"Endpoint switched {'on' if endpoint.is_active else 'off'}."
            )
            return redirect("api_webhook_detail", pk=endpoint.pk)
        if action == "rotate":
            endpoint.secret = WebhookEndpoint.generate_secret()
            endpoint.save(update_fields=["secret"])
            log_action(request.user, "api.webhook_secret_rotated", endpoint, request=request)
            messages.success(request, "A new signing secret was generated.")
            return redirect("api_webhook_detail", pk=endpoint.pk)
        if action == "test":
            deliveries = dispatch_event(
                "webhook.ping",
                {"message": "EduSphere webhook test", "requested_by": request.user.username},
                institution=institution,
                endpoint_ids=[endpoint.pk],
            )
            if deliveries:
                deliver(deliveries[0])
            messages.success(request, "Test event sent.")
            return redirect("api_webhook_detail", pk=endpoint.pk)
        if action == "delete":
            log_action(request.user, "api.webhook_deleted", endpoint, request=request)
            endpoint.delete()
            messages.success(request, "Webhook endpoint removed.")
            return redirect("api_webhooks")
        if action == "retry":
            delivery = get_object_or_404(
                WebhookDelivery, pk=request.POST.get("delivery"), endpoint=endpoint
            )
            delivery.status = WebhookDelivery.Status.PENDING
            delivery.next_attempt_at = delivery.created
            delivery.save(update_fields=["status", "next_attempt_at"])
            deliver(delivery)
            messages.success(request, "Delivery retried.")
            return redirect("api_webhook_detail", pk=endpoint.pk)

    deliveries = endpoint.deliveries.select_related("endpoint")[:100]
    return render(
        request,
        "api/webhook_detail.html",
        {
            "endpoint": endpoint,
            "deliveries": deliveries,
            "events": EVENTS,
            "event_names": EVENT_NAMES,
        },
    )


@login_required
@require_POST
def webhook_delete(request, pk):
    _require(request)
    institution = _institution(request)
    endpoint = get_object_or_404(WebhookEndpoint, pk=pk, institution=institution)
    log_action(request.user, "api.webhook_deleted", endpoint, request=request)
    endpoint.delete()
    messages.success(request, "Webhook endpoint removed.")
    return redirect("api_webhooks")
