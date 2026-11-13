"""API keys and webhook subscriptions.

Neither model uses :class:`core.models.TenantModel`. A key has to be looked up
before the institution is known (the key itself tells us which institution the
caller belongs to), and a webhook endpoint is reached by a background worker
that may not have a "current institution" at all. Both carry an explicit
``institution`` foreign key and every read is scoped by hand.
"""

import hashlib
import hmac
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

API_KEY_PREFIX = "esk"
API_KEY_HASH_ALGORITHM = "sha256"


def _hash_secret(secret):
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


class ApiKey(models.Model):
    """A named key belonging to one user of one institution.

    Only the SHA-256 digest of the secret is stored. The raw key is shown once
    when it is issued and can never be recovered afterwards.
    """

    institution = models.ForeignKey(
        "core.Institution", on_delete=models.CASCADE, related_name="api_keys"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_keys"
    )
    name = models.CharField(max_length=100, blank=True)
    prefix = models.CharField(max_length=24, unique=True)
    key_hash = models.CharField(max_length=64)
    scopes = models.JSONField(
        default=list,
        blank=True,
        help_text="Permission codes this key may use. Empty means every code its user holds.",
    )
    is_active = models.BooleanField(default=True)
    created = models.DateTimeField(auto_now_add=True)
    last_used = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created"]
        indexes = [models.Index(fields=["prefix", "is_active"])]

    def __str__(self):
        return f"{self.name or 'API key'} ({self.prefix})"

    @classmethod
    def issue(cls, institution, user, name="", scopes=None):
        """Create a key and return ``(key, raw_value)``.

        The raw value is only available here; callers must show it once.
        """
        if getattr(user, "institution_id", None) != institution.pk:
            raise ValueError("The user does not belong to that institution.")
        prefix = secrets.token_hex(6)
        secret = secrets.token_urlsafe(32)
        key = cls.objects.create(
            institution=institution,
            user=user,
            name=(name or "API key")[:100],
            prefix=prefix,
            key_hash=_hash_secret(secret),
            scopes=list(scopes or []),
        )
        return key, f"{API_KEY_PREFIX}_{prefix}_{secret}"

    @staticmethod
    def split(raw):
        """Split ``esk_<prefix>_<secret>`` into its parts, or return None."""
        if not raw:
            return None
        raw = raw.strip()
        parts = raw.split("_", 2)
        if len(parts) != 3 or parts[0] != API_KEY_PREFIX or not parts[1] or not parts[2]:
            return None
        return parts[1], parts[2]

    def matches(self, secret):
        return hmac.compare_digest(self.key_hash, _hash_secret(secret))

    def permits(self, code):
        """True when the key's scope allows this permission code."""
        return not self.scopes or code in self.scopes

    def touch(self):
        ApiKey.objects.filter(pk=self.pk).update(last_used=timezone.now())


class WebhookEndpoint(models.Model):
    """One URL that receives signed event payloads."""

    institution = models.ForeignKey(
        "core.Institution", on_delete=models.CASCADE, related_name="webhook_endpoints"
    )
    name = models.CharField(max_length=100)
    url = models.URLField(max_length=500)
    secret = models.CharField(max_length=100)
    events = models.JSONField(
        default=list,
        blank=True,
        help_text="Event names to send. Empty means every event.",
    )
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="webhook_endpoints_created",
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["institution", "is_active"])]

    def __str__(self):
        return self.name

    @classmethod
    def generate_secret(cls):
        return f"whsec_{secrets.token_urlsafe(32)}"

    def subscribes_to(self, event):
        return not self.events or event in self.events


class WebhookDelivery(models.Model):
    """One attempt-tracked delivery of one event to one endpoint."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        DELIVERED = "delivered", "Delivered"
        FAILED = "failed", "Failed"
        ABANDONED = "abandoned", "Abandoned"

    endpoint = models.ForeignKey(
        WebhookEndpoint, on_delete=models.CASCADE, related_name="deliveries"
    )
    event = models.CharField(max_length=80, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    response_body = models.TextField(blank=True)
    error = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created"]
        indexes = [models.Index(fields=["status", "next_attempt_at"])]

    def __str__(self):
        return f"{self.event} -> {self.endpoint.name} ({self.status})"

    @property
    def signature(self):
        return sign_webhook(self.endpoint.secret, self.payload)


def sign_webhook(secret, payload, timestamp=None):
    """Return the ``X-EduSphere-Signature`` header value for a payload.

    The signed text is ``<unix timestamp>.<compact json>`` so a receiver can
    reject a replay outside a short window.
    """
    import json

    if timestamp is None:
        timestamp = int(timezone.now().timestamp())
    body = json.dumps(payload, separators=(",", ":"), default=str, sort_keys=True)
    digest = hmac.new(
        secret.encode("utf-8"), f"{timestamp}.{body}".encode(), hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={digest}"
