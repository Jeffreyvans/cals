"""Notifications: in-app messages plus outbound email, SMS and WhatsApp.

Providers are pluggable. In development the console provider prints the
message so the flow can be tested without a gateway account.
"""

import logging

from django.utils import timezone

from core.models import Notification, OutboundMessage

logger = logging.getLogger(__name__)


class BaseProvider:
    """What every message provider must do."""

    name = "base"

    def send(self, channel, recipient, subject, body, html_body=""):
        """Send the message. Returns a dict with 'ok' and a 'detail' string."""
        raise NotImplementedError


class ConsoleProvider(BaseProvider):
    """Prints the message instead of sending it. Used in development."""

    name = "console"

    def send(self, channel, recipient, subject, body, html_body=""):
        logger.info("[%s] to=%s subject=%s body=%s", channel, recipient, subject, body)
        print(f"[{channel}] to {recipient}: {subject} {body}")
        return {"ok": True, "detail": "printed to console"}


class ResendProvider(BaseProvider):
    """Sends email through Resend's HTTPS API."""

    name = "resend"

    def send(self, channel, recipient, subject, body, html_body=""):
        import json
        from urllib.error import HTTPError, URLError
        from urllib.request import Request, urlopen

        from django.conf import settings

        api_key = getattr(settings, "RESEND_API_KEY", "")
        if not api_key:
            return {"ok": False, "detail": "RESEND_API_KEY is not set"}
        payload = {
            "from": getattr(settings, "RESEND_FROM", "") or settings.EMAIL_FROM,
            "to": [recipient],
            "subject": subject or "EduSphere",
            "text": body,
        }
        if html_body:
            payload["html"] = html_body
        data = json.dumps(payload).encode("utf-8")
        request = Request(
            getattr(settings, "RESEND_API_URL", "https://api.resend.com/emails"),
            data=data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=15) as response:
                detail = response.read().decode("utf-8", errors="replace")
            return {"ok": 200 <= response.status < 300, "detail": detail[:500]}
        except (HTTPError, URLError, OSError) as exc:
            return {"ok": False, "detail": str(exc)[:500]}


class SmtpProvider(BaseProvider):
    """Sends email over SMTP. Configured through environment variables."""

    name = "smtp"

    def send(self, channel, recipient, subject, body, html_body=""):
        from django.conf import settings
        from django.core.mail import EmailMultiAlternatives

        try:
            message = EmailMultiAlternatives(
                subject or "",
                body,
                settings.EMAIL_FROM or None,
                [recipient],
            )
            if html_body:
                message.attach_alternative(html_body, "text/html")
            sent = message.send(fail_silently=False)
        except Exception as exc:  # pragma: no cover - depends on the mail server
            return {"ok": False, "detail": str(exc)}
        return {"ok": bool(sent), "detail": f"{sent} message(s) sent"}


class StubSmsProvider(BaseProvider):
    """Placeholder for a real SMS gateway (for example NetOne or Econet)."""

    name = "sms-stub"

    def send(self, channel, recipient, subject, body, html_body=""):
        logger.info("[sms-stub] to=%s body=%s", recipient, body)
        return {"ok": True, "detail": "stub provider accepted the message"}


class StubWhatsAppProvider(BaseProvider):
    """Placeholder for a real WhatsApp Business provider."""

    name = "whatsapp-stub"

    def send(self, channel, recipient, subject, body, html_body=""):
        logger.info("[whatsapp-stub] to=%s body=%s", recipient, body)
        return {"ok": True, "detail": "stub provider accepted the message"}


def get_provider(channel):
    """The provider used for a channel, chosen by environment settings."""
    from django.conf import settings

    if channel == OutboundMessage.Channel.EMAIL:
        if getattr(settings, "RESEND_API_KEY", ""):
            return ResendProvider()
        if getattr(settings, "EMAIL_BACKEND", "") and "locmem" not in settings.EMAIL_BACKEND:
            return SmtpProvider()
        return ConsoleProvider()
    if channel == OutboundMessage.Channel.SMS:
        return StubSmsProvider()
    if channel == OutboundMessage.Channel.WHATSAPP:
        return StubWhatsAppProvider()
    return ConsoleProvider()


def notify(user, title, body="", link="", category="general", institution=None):
    """Create one in-app notification."""
    from core.tenancy import current_institution_id

    institution_id = (
        institution.pk if institution is not None else getattr(user, "institution_id", None)
    ) or current_institution_id()
    return Notification.objects.create(
        institution_id=institution_id,
        user=user,
        title=title[:150],
        body=body,
        link=link[:200],
        category=category,
    )


def notify_users(users, title, body="", link="", category="general", institution=None):
    """Create the same in-app notification for several users."""
    users = [u for u in users if u is not None]
    return [notify(u, title, body, link, category, institution) for u in users]


def queue_message(
    channel, recipient, body, subject="", institution=None, related_object=None, html_body=""
):
    """Queue an outbound message for sending."""
    from core.tenancy import current_institution_id

    institution_id = institution.pk if institution is not None else current_institution_id()
    obj_type = ""
    obj_id = ""
    if related_object is not None:
        obj_type = related_object.__class__.__name__
        obj_id = str(related_object.pk)
    return OutboundMessage.objects.create(
        institution_id=institution_id,
        channel=channel,
        recipient=recipient,
        subject=subject,
        body=body,
        html_body=html_body,
        related_object_type=obj_type,
        related_object_id=obj_id,
    )


def render_email_html(template_name, context):
    """Render a brown HTML email from a template. Returns an empty string on failure."""
    from django.template.loader import render_to_string

    try:
        return render_to_string(template_name, context)
    except Exception:  # pragma: no cover - a broken template must not stop the send
        logger.exception("Could not render email template %s", template_name)
        return ""


def send_message(message):
    """Try to send one queued message. Returns the updated message."""
    provider = get_provider(message.channel)
    result = provider.send(
        message.channel,
        message.recipient,
        message.subject,
        message.body,
        getattr(message, "html_body", ""),
    )
    message.provider = provider.name
    message.provider_response = result.get("detail", "")
    if result.get("ok"):
        message.status = OutboundMessage.Status.SENT
        message.sent_at = timezone.now()
    else:
        message.status = OutboundMessage.Status.FAILED
        message.retries += 1
    message.save(update_fields=["status", "sent_at", "provider", "provider_response", "retries"])
    return message


def send_pending(limit=100):
    """Send queued messages. Called by the Celery task."""
    sent = 0
    for message in OutboundMessage.objects.filter(status=OutboundMessage.Status.QUEUED)[:limit]:
        send_message(message)
        sent += 1
    return sent


def unread_count(user):
    """How many unread notifications this user has."""
    return Notification.objects.filter(user=user, read_at__isnull=True).count()


def mark_read(user, notification_id):
    """Mark one notification as read. Returns True when it worked."""
    notification = Notification.objects.filter(id=notification_id, user=user).first()
    if notification is None or notification.read_at is not None:
        return False
    notification.read_at = timezone.now()
    notification.save(update_fields=["read_at"])
    return True


def mark_all_read(user):
    """Mark every notification for this user as read."""
    return Notification.objects.filter(user=user, read_at__isnull=True).update(read_at=timezone.now())
