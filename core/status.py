"""Public system-status snapshot.

This page is intentionally honest: a component is only called *operational*
when the platform can actually prove it. Configuration-only components are
reported as *not configured* instead of pretending everything is healthy.

Nothing here exposes credentials, tenant data or message content.
"""

from datetime import timedelta

from django.conf import settings
from django.db import connection
from django.db.models import Count
from django.utils import timezone

from core.models import OutboundMessage

OPERATIONAL = "operational"
DEGRADED = "degraded"
OUTAGE = "outage"
NOT_CONFIGURED = "not_configured"

LABELS = {
    OPERATIONAL: "Operational",
    DEGRADED: "Degraded",
    OUTAGE: "Outage",
    NOT_CONFIGURED: "Not configured",
}


def _database():
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return OUTAGE, "The database did not answer a health query."
    return OPERATIONAL, "The database answered a health query."


def _email():
    backend = getattr(settings, "EMAIL_BACKEND", "")
    if "console" in backend or "locmem" in backend:
        return NOT_CONFIGURED, "Email is using a development backend; nothing is delivered."
    since = timezone.now() - timedelta(hours=24)
    breakdown = OutboundMessage.all_objects.filter(
        channel=OutboundMessage.Channel.EMAIL, created__gte=since,
        status__in=(OutboundMessage.Status.FAILED, OutboundMessage.Status.SENT),
    ).values("status").annotate(total=Count("pk"))
    counts = {row["status"]: row["total"] for row in breakdown}
    failed = counts.get(OutboundMessage.Status.FAILED, 0)
    sent = counts.get(OutboundMessage.Status.SENT, 0)
    if failed and not sent:
        return DEGRADED, f"{failed} email(s) failed in the last 24 hours with no successes."
    if failed:
        return DEGRADED, f"{failed} of {failed + sent} email(s) failed in the last 24 hours."
    return OPERATIONAL, f"{sent} email(s) delivered in the last 24 hours."


def _storage():
    storage = settings.STORAGES.get("default", {})
    backend = storage.get("BACKEND", "")
    if "FileSystemStorage" in backend:
        return NOT_CONFIGURED, "Uploads use local disk; configure object storage before go-live."
    return OPERATIONAL, "Uploads use the configured object storage backend."


def _payments():
    integration_id = getattr(settings, "PAYNOW_INTEGRATION_ID", "")
    integration_key = getattr(settings, "PAYNOW_INTEGRATION_KEY", "")
    if not (integration_id and integration_key):
        return NOT_CONFIGURED, "No online payment gateway credentials are configured."
    mode = getattr(settings, "PAYNOW_MODE", "test")
    if mode != "live":
        return DEGRADED, f"Online payments are in {mode!r} mode; no live money is taken."
    return OPERATIONAL, "The online payment gateway is in live mode."


def system_status_snapshot():
    """Return an overall status plus one entry per component."""
    checks = (
        ("Database", _database),
        ("Email delivery", _email),
        ("Object storage", _storage),
        ("Online payments", _payments),
    )
    components = []
    for name, check in checks:
        status, note = check()
        components.append({"name": name, "status": status, "note": note})
    statuses = {entry["status"] for entry in components}
    if OUTAGE in statuses:
        overall = OUTAGE
    elif DEGRADED in statuses:
        overall = DEGRADED
    elif NOT_CONFIGURED in statuses:
        overall = DEGRADED
    else:
        overall = OPERATIONAL
    return {
        "overall": overall,
        "overall_label": LABELS[overall],
        "components": [
            {**entry, "label": LABELS[entry["status"]]} for entry in components
        ],
        "checked_at": timezone.now(),
    }
