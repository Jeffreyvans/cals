"""Verified delivery webhooks for Resend and Mailjet.

Resend signs events with Svix headers; Mailjet recommends HTTP basic auth on
the webhook URL. Both handlers record delivered/bounced/blocked/spam events and
suppress addresses on hard bounces or complaints so a later message cannot be
queued to a dead or hostile address.
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

from core.errors import error_json
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
        return error_json(request, "CSRF", 400, detail="Invalid webhook signature.")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return error_json(request, "INPUT-INVALID", 400, detail="Invalid webhook payload.")

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


# ---------- Mailjet ----------

# Mailjet sends a JSON array of events. These are the event names we record;
# opens and clicks are ignored (they are tracking, not delivery outcomes).
_MAILJET_KINDS = {
    "sent": EmailEvent.Kind.SENT,
    "delivered": EmailEvent.Kind.DELIVERED,
    "bounce": EmailEvent.Kind.BOUNCED,
    "blocked": EmailEvent.Kind.BLOCKED,
    "spam": EmailEvent.Kind.SPAM,
}
# Bounce sub-types that are permanent (hard) and must suppress the address.
_MAILJET_HARD_BOUNCES = {
    "hardbounce", "blocked", "unsubscribe", "emailnotfound", "invalidemail",
    "badaddress", "accountproblem", "typofdomain", "cannotroute",
    "unknownuser", "mailboxfull",
}


def _verify_mailjet_auth(request):
    """Verify the basic-auth credentials configured on the Mailjet webhook.

    Mailjet does not sign webhook bodies. The documented protection is to put
    credentials in the callback URL (``https://user:pass@example.com/...``),
    which Mailjet then sends as an ``Authorization: Basic ...`` header. The
    secret may be ``user:pass`` or just ``pass`` (any username is accepted in
    that case).
    """
    secret = getattr(settings, "MAILJET_WEBHOOK_SECRET", "")
    if not secret:
        logger.warning("Mailjet webhook received without MAILJET_WEBHOOK_SECRET")
        return False
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(header.split(None, 1)[1]).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    supplied_user, _sep, supplied_password = decoded.partition(":")
    expected_user, _sep, expected_password = secret.partition(":")
    if expected_password and expected_user:
        return hmac.compare_digest(decoded, secret)
    if expected_password:
        return hmac.compare_digest(supplied_password, expected_password)
    return hmac.compare_digest(f"{supplied_user}:{supplied_password}", secret)


def _mailjet_message(data, recipient):
    """Find the OutboundMessage a Mailjet event refers to, if we have one."""
    message_id = str(data.get("MessageID") or data.get("message_id") or "").strip()
    custom_id = str(data.get("CustomID") or "").strip()
    qs = OutboundMessage.all_objects
    if custom_id.isdigit():
        found = qs.filter(pk=int(custom_id)).first()
        if found is not None:
            return found
    if message_id:
        found = qs.filter(provider_message_id=message_id).first()
        if found is not None:
            return found
    return None


@csrf_exempt
@require_POST
def mailjet_webhook(request):
    """Record Mailjet delivery events and suppress dead/hostile addresses."""
    if not _verify_mailjet_auth(request):
        return error_json(request, "AUTH-FAILED", 403, detail="Invalid webhook credentials.")
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return error_json(request, "INPUT-INVALID", 400, detail="Invalid webhook payload.")
    events = payload if isinstance(payload, list) else [payload]

    recorded = 0
    with transaction.atomic():
        for event in events:
            if not isinstance(event, dict):
                continue
            name = str(event.get("event") or "").lower()
            kind = _MAILJET_KINDS.get(name)
            if kind is None:
                continue
            recipient = str(event.get("email") or event.get("Recipient") or "").strip()
            if not recipient:
                continue
            message = _mailjet_message(event, recipient)
            institution_id = message.institution_id if message is not None else None
            if institution_id is None:
                from core.tenancy import default_institution_id

                institution_id = default_institution_id()
            if institution_id is None:
                continue
            subtype = str(
                event.get("bounce_type") or event.get("blocked_type")
                or event.get("reason") or ""
            ).lower()
            detail = f"{name}: {subtype}" if subtype else name
            EmailEvent.objects.create(
                institution_id=institution_id,
                message=message,
                recipient=recipient,
                kind=kind,
                provider_event_id=str(
                    event.get("MessageID") or event.get("CustomID") or ""
                )[:255],
                detail=detail[:500],
            )
            recorded += 1
            suppress = (
                (kind in {EmailEvent.Kind.BOUNCED, EmailEvent.Kind.BLOCKED}
                 and (not subtype or subtype in _MAILJET_HARD_BOUNCES))
                or kind == EmailEvent.Kind.SPAM
            )
            if suppress and not EmailSuppression.objects.filter(
                email__iexact=recipient
            ).exists():
                reason = (
                    EmailSuppression.Reason.COMPLAINED
                    if kind == EmailEvent.Kind.SPAM
                    else EmailSuppression.Reason.BOUNCED
                )
                EmailSuppression.objects.create(email=recipient.lower(), reason=reason)
    return JsonResponse({"recorded": recorded})
