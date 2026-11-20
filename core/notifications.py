"""Notifications: in-app messages plus outbound email, SMS and WhatsApp.

All email is delivered through Django's single configured email backend:
Anymail's Mailjet HTTPS backend on Render (Brevo and Resend remain selectable
via ``EMAIL_PROVIDER``), the console backend in local development and Django's
in-memory backend in tests. Nothing here opens an SMTP connection, so the
blocked SMTP ports on Render's free plan are never used. Every attempt is
recorded in :class:`~core.models.OutboundMessage`.

``send_email`` is the one entry point every application email uses. It creates
an OutboundMessage (type, recipient, institution, HTML and text parts) and
hands it to ``send_message``, which retries failures and never raises.
"""
import hashlib
import logging
import re
from datetime import timedelta
from email.utils import formataddr, parseaddr

from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives, get_connection
from django.utils import timezone

from config.brand import PRODUCT_NAME
from core.email_types import ADMIN_ERROR, EMAIL_TYPE_CODES, GENERAL
from core.models import EmailSuppression, Notification, OutboundMessage

logger = logging.getLogger(__name__)

# The user-facing wording when delivery fails. A failed email must never break
# the request that triggered it.
EMAIL_FAILURE_MESSAGE = (
    "External service problem: The email service did not respond (EXT-EMAIL). "
    "Try again in a few minutes."
)

_SECRET_PATTERNS = (
    (re.compile(r"(?i)bearer\s+[\w.\-]+"), "Bearer [redacted]"),
    (re.compile(r"(?i)(api[_-]?key|token|secret|password)(\s*[=:]\s*)(\S+)"),
     r"\1\2[redacted]"),
    (re.compile(r"(?i)([?&](?:token|key|code|sig|signature)=)[^&\s]+"),
     r"\1[redacted]"),
    (re.compile(r"\b[0-9a-f]{32,}\b", re.I), "[redacted]"),
)

try:  # pragma: no cover - Celery is optional
    from celery import current_app, shared_task
except ImportError:  # pragma: no cover - the common small-school case
    current_app = None
    shared_task = None


def sanitize_detail(value, limit=500):
    """Strip keys, tokens and token-bearing links from provider error text."""
    text = "" if value is None else str(value)
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text[:limit]


class BaseProvider:
    """What every message provider must do."""

    name = "base"

    def send(self, channel, recipient, subject, body, html_body="",
             from_email=None, reply_to=None):
        """Send the message. Returns a dict with 'ok' and a 'detail' string."""
        raise NotImplementedError


class DjangoEmailProvider(BaseProvider):
    """Send email through Django's configured backend (Anymail on Render).

    There is no HTTP client of our own here: django-anymail translates the
    Django message into a Mailjet (or Brevo/Resend) HTTPS API call. That keeps
    one code path for every message and makes Django's locmem backend work in
    tests.
    """

    def __init__(self, name=None):
        self.name = name or _email_backend_name()

    def send(self, channel, recipient, subject, body, html_body="",
             from_email=None, reply_to=None):
        connection = get_connection()
        email = EmailMultiAlternatives(
            subject or "(no subject)",
            body,
            from_email or _from_email(),
            [recipient],
            reply_to=reply_to or None,
            connection=connection,
        )
        if html_body:
            email.attach_alternative(html_body, "text/html")
        try:
            sent = email.send(fail_silently=False)
        except Exception as exc:  # never leak a provider response to a view
            return {"ok": False, "detail": sanitize_detail(exc)}
        message_id = getattr(getattr(email, "anymail_status", None), "message_id", "")
        return {
            "ok": bool(sent),
            "detail": f"{sent} message(s) accepted by {self.name}",
            "message_id": str(message_id or "")[:255],
        }


class StubSmsProvider(BaseProvider):
    """Placeholder for a real SMS gateway (for example NetOne or Econet)."""

    name = "sms-stub"

    def send(self, channel, recipient, subject, body, html_body="",
             from_email=None, reply_to=None):
        logger.warning("[sms-stub] no SMS provider configured; message not delivered to=%s",
                       recipient)
        return {"ok": False, "detail": "SMS is not configured in this deployment"}


class StubWhatsAppProvider(BaseProvider):
    """Placeholder for a real WhatsApp Business provider."""

    name = "whatsapp-stub"

    def send(self, channel, recipient, subject, body, html_body="",
             from_email=None, reply_to=None):
        logger.warning(
            "[whatsapp-stub] no WhatsApp provider configured; message not delivered to=%s",
            recipient,
        )
        return {"ok": False, "detail": "WhatsApp is not configured in this deployment"}


def _email_backend_name():
    """A short, secret-free name for the backend currently in use."""
    backend = getattr(settings, "EMAIL_BACKEND", "").lower()
    for name in ("mailjet", "brevo", "resend", "console", "locmem", "smtp", "filebased"):
        if name in backend:
            return name
    return "django"


def get_provider(channel):
    """The provider used for a channel, chosen by environment settings."""
    if channel == OutboundMessage.Channel.EMAIL:
        return DjangoEmailProvider()
    if channel == OutboundMessage.Channel.SMS:
        return StubSmsProvider()
    if channel == OutboundMessage.Channel.WHATSAPP:
        return StubWhatsAppProvider()
    return BaseProvider()


def _from_email(institution=None):
    """Institution name as display name, address on the verified domain.

    The address always comes from DEFAULT_FROM_EMAIL; only the human-readable
    display name changes, so a tenant can never send from an unverified domain.
    """
    default = getattr(settings, "DEFAULT_FROM_EMAIL", "") or f"{PRODUCT_NAME} <no-reply@edusphere.local>"
    _name, address = parseaddr(default)
    if not address:
        address = getattr(settings, "SERVER_EMAIL", "") or "no-reply@edusphere.local"
    display = getattr(institution, "name", "") or PRODUCT_NAME
    return formataddr((display, address))


def _reply_to(institution=None):
    contact = getattr(institution, "contact_email", "") if institution is not None else ""
    contact = contact or getattr(settings, "DEFAULT_REPLY_TO_EMAIL", "")
    return [contact] if contact else None


def absolute_url(path):
    """Build a public link from SITE_URL, never from the request host."""
    base = (getattr(settings, "SITE_URL", "") or "").rstrip("/")
    if not path:
        return base
    if not str(path).startswith("/"):
        path = "/" + str(path)
    return f"{base}{path}" if base else path


def _generic_html(message):
    """A themed HTML part for messages that did not supply one."""
    from core.notifications_html import render_generic_email_html

    return render_generic_email_html(
        message.subject or PRODUCT_NAME, message.body, message.institution
    )


def _rate_key(scope, kind, value):
    digest = hashlib.sha256(str(value).strip().lower().encode("utf-8")).hexdigest()[:24]
    return f"edusphere.email.{scope}.{kind}.{digest}"


def _client_ip(request):
    if request is None:
        return ""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _rate_targets(scope, address="", request=None, ip=""):
    targets = []
    if address:
        limit = getattr(settings, "EMAIL_VERIFY_LIMIT", 5)
        if scope == "reset":
            limit = getattr(settings, "EMAIL_RESET_LIMIT", 5)
        targets.append((_rate_key(scope, "address", address), limit))
    ip = ip or _client_ip(request)
    if ip:
        targets.append((_rate_key(scope, "ip", ip), getattr(settings, "EMAIL_IP_LIMIT", 20)))
    return targets


def email_rate_limited(scope, address="", request=None, ip=""):
    """True when an address or an IP has reached its email limit."""
    return any(cache.get(key, 0) >= limit
               for key, limit in _rate_targets(scope, address, request, ip))


def record_email_attempt(scope, address="", request=None, ip=""):
    """Record one attempt against the address and IP buckets."""
    window = getattr(settings, "EMAIL_RATE_WINDOW_SECONDS", 3600)
    for key, _limit in _rate_targets(scope, address, request, ip):
        cache.set(key, cache.get(key, 0) + 1, window)


def _bulk_key(institution):
    pk = institution.pk if institution is not None else "platform"
    return f"edusphere.email.bulk.{pk}"


def bulk_email_limit_reached(institution, limit=None):
    """True when an institution has reached its per-window bulk cap."""
    cap = limit if limit is not None else getattr(settings, "EMAIL_BULK_LIMIT", 500)
    return cache.get(_bulk_key(institution), 0) >= cap


def record_bulk_email(institution, count=1):
    window = getattr(settings, "EMAIL_RATE_WINDOW_SECONDS", 3600)
    key = _bulk_key(institution)
    cache.set(key, cache.get(key, 0) + count, window)


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
    channel, recipient, body, subject="", institution=None, related_object=None,
    html_body="", email_type=GENERAL,
):
    """Queue an outbound message for sending.

    This is the single creation point for every outbound message. ``email_type``
    is one of :data:`core.email_types.EMAIL_TYPE_CODES` so the Email health
    page and the reports can count messages by type.
    """
    from core.tenancy import current_institution_id

    if email_type not in EMAIL_TYPE_CODES:
        raise ValueError(f"Unknown email type: {email_type!r}")
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
        email_type=email_type,
        related_object_type=obj_type,
        related_object_id=obj_id,
    )


def send_email(email_type, recipient, subject, body, html_body="", institution=None,
               related_object=None, reply_to=None, send=True):
    """The one central entry point for application email.

    Builds a typed OutboundMessage with a plain-text and an HTML part, then
    sends it through :func:`send_message` (or only queues it when ``send`` is
    False, for bulk jobs that use ``send_pending``). It never raises on a
    provider failure: check the returned message's status instead.
    """
    message = queue_message(
        OutboundMessage.Channel.EMAIL, recipient, body, subject=subject,
        institution=institution, related_object=related_object,
        html_body=html_body or _generic_html_for(subject, body, institution),
        email_type=email_type,
    )
    if reply_to:
        message.reply_to_override = ", ".join(reply_to)
    if send:
        return send_message(message)
    return message


def send_platform_email(subject, body, recipient, reply_to=None, institution=None,
                        related_object=None, html_body="", email_type=GENERAL):
    """Queue and send a platform message (contact, demo and check_email)."""
    return send_email(
        email_type, recipient, subject, body, html_body=html_body,
        institution=institution, related_object=related_object, reply_to=reply_to,
    )


def send_admin_error_email(subject, body, recipient=None, institution=None, related_object=None):
    """Send an error/alert email to administrators through the central pipeline."""
    from django.conf import settings as django_settings

    address = recipient or getattr(django_settings, "SERVER_EMAIL", "") or _from_email()
    return send_email(ADMIN_ERROR, address, subject, body, institution=institution,
                      related_object=related_object)


def _generic_html_for(subject, body, institution=None):
    from core.notifications_html import render_generic_email_html

    return render_generic_email_html(subject, body, institution)


def render_email_html(template_name, context):
    """Render a brown HTML email from a template. Returns an empty string on failure."""
    from django.template.loader import render_to_string

    try:
        return render_to_string(template_name, context)
    except Exception:  # pragma: no cover - a broken template must not stop the send
        logger.exception("Could not render email template %s", template_name)
        return ""


def _suppressed(recipient):
    return EmailSuppression.objects.filter(email__iexact=recipient.strip()).exists()


def _retry_delay(attempt):
    base = max(0, getattr(settings, "EMAIL_RETRY_BACKOFF_SECONDS", 60))
    return base * (2 ** max(0, attempt - 1))


def _should_send_async():
    if not getattr(settings, "EMAIL_ASYNC", False) or shared_task is None:
        return False
    # Only dispatch when a broker is actually configured (or tasks run eager).
    conf = getattr(current_app, "conf", None) if current_app is not None else None
    return bool(conf and (conf.broker_url or conf.task_always_eager))


def _attempt_delivery(message):
    """Try one delivery. Never raises, and records every attempt."""
    try:
        if message.channel != OutboundMessage.Channel.EMAIL:
            provider = get_provider(message.channel)
            result = provider.send(
                message.channel, message.recipient, message.subject, message.body,
                getattr(message, "html_body", ""),
            )
            message.provider = provider.name
            message.provider_response = sanitize_detail(result.get("detail", ""))
            message.status = (OutboundMessage.Status.SENT if result.get("ok")
                              else OutboundMessage.Status.FAILED)
            if result.get("ok"):
                message.sent_at = timezone.now()
            message.save(update_fields=[
                "status", "sent_at", "provider", "provider_response", "retries",
            ])
            return message

        if _suppressed(message.recipient):
            message.provider = get_provider(message.channel).name
            message.provider_response = "Address suppressed after a bounce or complaint"
            message.status = OutboundMessage.Status.CANCELLED
            message.next_attempt_at = None
            message.save(update_fields=[
                "status", "provider", "provider_response", "next_attempt_at",
            ])
            return message

        provider = get_provider(message.channel)
        html_body = getattr(message, "html_body", "") or _generic_html(message)
        message.html_body = html_body
        reply_to = getattr(message, "reply_to_override", None)
        if not reply_to:
            reply_to = _reply_to(message.institution)
        elif isinstance(reply_to, str):
            reply_to = [reply_to]
        result = provider.send(
            message.channel,
            message.recipient,
            message.subject,
            message.body,
            html_body,
            from_email=_from_email(message.institution),
            reply_to=reply_to,
        )
        if getattr(message, "reply_to_override", None):
            delattr(message, "reply_to_override")
        message.provider = provider.name
        message.provider_response = sanitize_detail(result.get("detail", ""))
        message.provider_message_id = sanitize_detail(result.get("message_id", ""), 255)
        if result.get("ok"):
            message.status = OutboundMessage.Status.SENT
            message.sent_at = timezone.now()
            message.next_attempt_at = None
        else:
            message.retries += 1
            max_retries = max(1, getattr(settings, "EMAIL_MAX_RETRIES", 5))
            if message.retries < max_retries:
                message.status = OutboundMessage.Status.QUEUED
                message.next_attempt_at = timezone.now() + timedelta(
                    seconds=_retry_delay(message.retries))
                if _should_send_async():
                    _schedule_retry(message)
            else:
                message.status = OutboundMessage.Status.FAILED
                message.next_attempt_at = None
        message.save(update_fields=[
            "status", "sent_at", "provider", "provider_response",
            "provider_message_id", "retries", "next_attempt_at", "html_body",
        ])
    except Exception as exc:  # a broken message must never break a request
        logger.warning("Email delivery failed for message %s: %s",
                       getattr(message, "pk", "?"), sanitize_detail(exc))
        try:
            message.status = OutboundMessage.Status.FAILED
            message.provider_response = sanitize_detail(exc)
            message.next_attempt_at = None
            message.save(update_fields=["status", "provider_response", "next_attempt_at"])
        except Exception:  # pragma: no cover - the database itself is down
            logger.exception("Could not record email failure")
    return message


def _schedule_retry(message):
    if shared_task is None:
        return
    try:
        send_message_task.apply_async(
            args=[message.pk], countdown=_retry_delay(message.retries))
    except Exception:  # pragma: no cover - broker temporarily unavailable
        logger.warning("Could not schedule an email retry")


def send_message(message, force_inline=False):
    """Send one queued message now, or hand it to Celery when configured.

    Returns the message with its updated status. This function is deliberately
    non-raising: callers can carry on even when the provider is down.
    """
    if message.channel == OutboundMessage.Channel.EMAIL and not force_inline and _should_send_async():
        if shared_task is not None:
            try:
                send_message_task.delay(message.pk)
            except Exception:  # pragma: no cover - fall back to inline delivery
                logger.warning("Celery dispatch failed; sending inline")
            else:
                return message
    return _attempt_delivery(message)


if shared_task is not None:  # pragma: no cover - depends on the deployment
    @shared_task(name="core.send_email_message")
    def send_message_task(message_id):
        message = OutboundMessage.all_objects.filter(pk=message_id).first()
        if message is None or message.status != OutboundMessage.Status.QUEUED:
            return None
        return _attempt_delivery(message)
else:  # pragma: no cover
    def send_message_task(message_id):
        message = OutboundMessage.all_objects.filter(pk=message_id).first()
        return _attempt_delivery(message) if message else None


def send_pending(limit=100):
    """Send due queued messages. Called by the Celery task or a cron command."""
    from django.db.models import Q

    due = OutboundMessage.objects.filter(
        Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=timezone.now()),
        status=OutboundMessage.Status.QUEUED,
    )[:limit]
    sent = 0
    for message in due:
        _attempt_delivery(message)
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
    return Notification.objects.filter(user=user, read_at__isnull=True).update(read_at=timezone.now())  # noqa: E501
