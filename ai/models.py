"""Tenant-scoped controls and the privacy-preserving AI usage log."""

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import TenantModel

from .features import ALL_FEATURES, FEATURE_LABELS, Feature


class AIFeatureSetting(TenantModel):
    """One institution's switch and limits for one AI feature.

    A missing row is always off. Defaults are deliberately conservative; an
    institution administrator has to open each feature explicitly.
    """

    institution = models.ForeignKey(
        "core.Institution", on_delete=models.CASCADE, related_name="ai_feature_settings"
    )
    feature = models.CharField(max_length=20, choices=Feature.choices)
    enabled = models.BooleanField(default=False)
    institution_daily_limit = models.PositiveIntegerField(default=100)
    institution_monthly_limit = models.PositiveIntegerField(default=1000)
    user_daily_limit = models.PositiveIntegerField(default=20)
    user_monthly_limit = models.PositiveIntegerField(default=200)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="ai_feature_settings_changed",
    )

    class Meta:
        unique_together = ("institution", "feature")
        ordering = ["feature"]

    def __str__(self):
        label = FEATURE_LABELS.get(self.feature, self.feature)
        return f"{label} at {self.institution.name}: {'on' if self.enabled else 'off'}"


class AIConsent(TenantModel):
    """Evidence that a named person accepted a specific disclosure.

    Older acceptances are kept for the audit trail. A withdrawal simply sets
    ``withdrawn_at``; nothing is deleted.
    """

    institution = models.ForeignKey(
        "core.Institution", on_delete=models.CASCADE, related_name="ai_consents"
    )
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="ai_consents"
    )
    feature = models.CharField(
        max_length=20,
        choices=[(ALL_FEATURES, "All AI features")] + list(Feature.choices),
        default=ALL_FEATURES,
    )
    version = models.CharField(max_length=20)
    shared_categories = models.JSONField(default=list, blank=True)
    recorded_categories = models.JSONField(default=list, blank=True)
    purpose = models.TextField(blank=True)
    accepted_at = models.DateTimeField(default=timezone.now, db_index=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-accepted_at", "-pk"]
        indexes = [models.Index(fields=["institution", "accepted_by", "feature"])]

    def __str__(self):
        return f"{self.accepted_by} accepted AI {self.feature} consent {self.version}"

    @property
    def is_active(self):
        return self.withdrawn_at is None


class AIPlatformSetting(models.Model):
    """Platform-wide kill switch. Row 1 exists lazily and defaults to off."""

    enabled = models.BooleanField(default=False)
    note = models.CharField(max_length=255, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="ai_platform_settings_changed",
    )

    class Meta:
        verbose_name = "AI platform setting"
        verbose_name_plural = "AI platform settings"

    def __str__(self):
        return f"AI platform kill switch: {'open' if self.enabled else 'closed'}"

    @classmethod
    def load(cls):
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj


class AIUsageLog(TenantModel):
    """One call or blocked attempt. Never stores a full prompt or answer."""

    class Status(models.TextChoices):
        SUCCESS = "success", "Succeeded"
        FAILURE = "failure", "Provider failed"
        BLOCKED = "blocked", "Blocked by a control"

    institution = models.ForeignKey(
        "core.Institution", on_delete=models.CASCADE, related_name="ai_usage_logs"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="ai_usage_logs",
    )
    feature = models.CharField(max_length=20)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SUCCESS)
    model_name = models.CharField(max_length=120, blank=True)
    tier = models.CharField(max_length=10, default="fast")
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_estimate = models.DecimalField(max_digits=14, decimal_places=6, default=0)
    prompt_summary = models.CharField(max_length=180, blank=True)
    created = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created", "-pk"]
        indexes = [
            models.Index(fields=["institution", "created"]),
            models.Index(fields=["user", "created"]),
        ]

    def __str__(self):
        return f"AI {self.feature} for {self.user_id}: {self.status} at {self.created:%Y-%m-%d}"
