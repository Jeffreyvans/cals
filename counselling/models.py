"""Counselling sessions and referrals. Notes stay with the counsellor."""

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import TenantModel


class CounsellingSession(TenantModel):
    class Kind(models.TextChoices):
        INDIVIDUAL = "individual", "Individual"
        GROUP = "group", "Group"
        CRISIS = "crisis", "Crisis"
        FOLLOW_UP = "follow_up", "Follow-up"

    student = models.ForeignKey(
        "academics.Student", on_delete=models.CASCADE, related_name="counselling_sessions"
    )
    counsellor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="counselling_sessions",
    )
    session_at = models.DateTimeField(default=timezone.now)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.INDIVIDUAL)
    topic = models.CharField(max_length=150)
    notes = models.TextField(blank=True)
    follow_up_on = models.DateField(null=True, blank=True)
    confidential = models.BooleanField(default=True)

    class Meta:
        ordering = ["-session_at", "-pk"]

    def __str__(self):
        return f"{self.student} - {self.topic}"


class Referral(TenantModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CONTACTED = "contacted", "Contacted"
        CLOSED = "closed", "Closed"
        DECLINED = "declined", "Declined"

    student = models.ForeignKey(
        "academics.Student", on_delete=models.CASCADE, related_name="counselling_referrals"
    )
    referred_to = models.CharField(max_length=150)
    reason = models.TextField()
    referred_on = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    referred_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="counselling_referrals_made",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-referred_on", "-pk"]

    def __str__(self):
        return f"{self.student} to {self.referred_to}"
