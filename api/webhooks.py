"""Webhook delivery: queue, sign, send, retry.

Events are queued as :class:`WebhookDelivery` rows so a slow or broken receiver
never holds up the request that caused the event. The ``deliver_webhooks``
management command sends due deliveries; a failed delivery is retried with a
growing delay and is abandoned after ``MAX_ATTEMPTS``.
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

from django.utils import timezone

from core.tenancy import current_institution_id

from .models import WebhookDelivery, WebhookEndpoint, sign_webhook

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10
MAX_ATTEMPTS = 6
RETRY_DELAYS = [60, 300, 900, 3600, 21600]  # one minute, then five, fifteen, an hour, six hours


def dispatch_event(event, payload, institution=None, endpoint_ids=None):
    """Queue one delivery per subscribed endpoint. Returns the new rows."""
    institution_id = (
        institution.pk if institution is not None else current_institution_id()
    )
    if institution_id is None:
        return []
    endpoints = WebhookEndpoint.objects.filter(institution_id=institution_id, is_active=True)
    if endpoint_ids is not None:
        endpoints = endpoints.filter(pk__in=endpoint_ids)
    deliveries = []
    for endpoint in endpoints:
        if endpoint.subscribes_to(event):
            deliveries.append(
                WebhookDelivery.objects.create(
                    endpoint=endpoint,
                    event=event,
                    payload=payload,
                    next_attempt_at=timezone.now(),
                )
            )
    return deliveries


def deliver(delivery, timeout=TIMEOUT_SECONDS):
    """Send one delivery and record the result. Returns the delivery."""
    endpoint = delivery.endpoint
    if not _valid_url(endpoint.url):
        return _fail(delivery, "The endpoint URL is not an http(s) URL.", status=None)

    body = json.dumps(delivery.payload, separators=(",", ":"), default=str, sort_keys=True)
    timestamp = int(timezone.now().timestamp())
    signature = sign_webhook(endpoint.secret, delivery.payload, timestamp)
    request = urllib.request.Request(
        endpoint.url,
        data=body.encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "EduSphere-Webhooks/1.0",
            "X-EduSphere-Event": delivery.event,
            "X-EduSphere-Delivery": str(delivery.pk),
            "X-EduSphere-Signature": signature,
        },
    )
    delivery.attempts += 1
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read(2000).decode("utf-8", errors="replace")
            delivery.status = WebhookDelivery.Status.DELIVERED
            delivery.response_status = response.status
            delivery.response_body = response_body
            delivery.error = ""
            delivery.delivered_at = timezone.now()
            delivery.save(
                update_fields=[
                    "attempts",
                    "status",
                    "response_status",
                    "response_body",
                    "error",
                    "delivered_at",
                ]
            )
    except urllib.error.HTTPError as exc:
        text = ""
        try:
            text = exc.read(2000).decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover - unreadable body
            text = ""
        _fail(delivery, f"HTTP {exc.code}", status=exc.code, response_body=text)
    except Exception as exc:  # network error, DNS failure, timeout
        _fail(delivery, str(exc))
    return delivery


def _fail(delivery, message, status=None, response_body=""):
    delivery.response_status = status
    delivery.response_body = response_body
    delivery.error = message[:2000]
    if delivery.attempts >= MAX_ATTEMPTS:
        delivery.status = WebhookDelivery.Status.ABANDONED
    else:
        delivery.status = WebhookDelivery.Status.FAILED
        delay = RETRY_DELAYS[min(delivery.attempts - 1, len(RETRY_DELAYS) - 1)]
        delivery.next_attempt_at = timezone.now() + timedelta(seconds=delay)
    delivery.save(
        update_fields=[
            "attempts",
            "status",
            "response_status",
            "response_body",
            "error",
            "next_attempt_at",
        ]
    )
    return delivery


def deliver_due(limit=100, delivery_id=None, now=None):
    """Send due deliveries. Used by the management command."""
    now = now or timezone.now()
    queryset = WebhookDelivery.objects.select_related("endpoint").filter(
        status__in=[WebhookDelivery.Status.PENDING, WebhookDelivery.Status.FAILED],
        next_attempt_at__lte=now,
    )
    if delivery_id is not None:
        queryset = queryset.filter(pk=delivery_id)
    sent = 0
    for delivery in queryset.order_by("next_attempt_at")[:limit]:
        deliver(delivery)
        sent += 1
    return sent


def _valid_url(url):
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)
