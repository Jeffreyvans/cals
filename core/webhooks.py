"""Signature-verified Resend delivery webhook.

Resend signs events with Svix headers. We record delivered, bounced and
complained events and suppress addresses on hard bounces or complaints so a
later message cannot be queued to a dead or hostile address.
"""

import base64
import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.models import EmailEvent, EmailSuppression, OutboundMessage

logger = logging.getLogger(__name__)

_KINDS = {
    "delivered": EmailEvent.Kind.DELIVERED,
    "bounced": EmailEvent.Kind.BOUNCED,
    "complained": EmailEvent.Kind.COMPLAINED,
}


def _verify_signature(body, request):
    """Verify the Svix signature. Never trust an unsigned webhook."""
    secret = getattr(settings, "RESEND_WEBHOOK_SECRET", "")
    if not secret:
        logger.warning("Resend webhook received without RESEND_WEBHOOK_SECRET")
        return False
    headers = {key.lower(): value for key, value in request.headers.items()}
    try:
        from svix.webhooks import Webhook

        Webhook(secret).verify(body, headers)
        return True
    except ImportError:  # pragma: no cover - svix ships with anymail[resend]
        pass
    except Exception:
        return False
    # Fallback for environments that cannot install svix.
    try:
        message_id = headers.get("svix-id", "")
        timestamp = headers.get("svix-timestamp", "")
        signatures = headers.get("svix-signature", "").split()
        raw = secret[6:] if secret.startswith("whsec_") else secret
        key = base64.b64decode(raw)
        expected = b"v1," + base64.b64encode(
            hmac.new(
                key,
                f"{message_id}.{timestamp}.".encode() + body + b".",
                hashlib.sha256,
            ).digest()
        )
        return any(
            hmac.compare_digest(signature.encode(), expected)
            for signature in signatures
            if signature.startswith("v1,")
        )
    except Exception:
        return False


def _event_type(payload):
    raw = str(payload.get("type") or "")
    for kind in _KINDS:
        if raw.endswith(kind) or raw == kind:
            return kind
    return ""


def _recipient(data):
    to = data.get("to") or data.get("email") or ""
    if isinstance(to, list):
        return str(to[0]) if to else ""
    return str(to)


@csrf_exempt
@require_POST
def resend_webhook(request):
    body = request.body
    if not _verify_signature(body, request):
        return JsonResponse({"detail": "invalid signature"}, status=400)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"detail": "invalid payload"}, status=400)

    kind = _event_type(payload)
    if not kind:
        return JsonResponse({"detail": "ignored"}, status=200)

    data = payload.get("data") or {}
    recipient = _recipient(data)
    if not recipient:
        return JsonResponse({"detail": "no recipient"}, status=200)

    with transaction.atomic():
        message = OutboundMessage.all_objects.filter(
            provider_message_id=data.get("email_id") or ""
        ).first() if data.get("email_id") else None
        institution_id = (
            message.institution_id if message is not None else None
        )
        if institution_id is None:
            from core.tenancy import default_institution_id

            institution_id = default_institution_id()
        if institution_id is None:
            return JsonResponse({"detail": "no institution"}, status=200)

        EmailEvent.objects.create(
            institution_id=institution_id,
            message=message,
            recipient=recipient,
            kind=_KINDS[kind],
            provider_event_id=str(payload.get("id") or "")[:255],
            detail=str(data.get("bounce") or "")[:500],
        )

        hard_bounce = kind != "bounced" or str(
            (data.get("bounce") or {}).get("type", "Permanent")
        ) in {"Permanent", "HardBounce", "hard_bounce"}
        if kind in {"bounced", "complained"} and hard_bounce:
            reason = (EmailSuppression.Reason.COMPLAINED if kind == "complained"
                      else EmailSuppression.Reason.BOUNCED)
            if not EmailSuppression.objects.filter(email__iexact=recipient).exists():
                EmailSuppression.objects.create(email=recipient.lower(), reason=reason)
    return HttpResponse("ok")
