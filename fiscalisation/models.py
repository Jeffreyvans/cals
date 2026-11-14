"""Fiscalisation data: devices, fiscal days and FDMS submissions.

Nothing here runs unless the ``fiscalisation`` module is switched on for the
institution *and* the institution is VAT-registered. The records are an
append-only trail of what was sent to ZIMRA and what came back.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import TenantModel
from finance.models import DocumentType


class FiscalDevice(TenantModel):
    """The ZIMRA-registered device (or virtual device) for an institution."""

    device_id = models.CharField(max_length=80, blank=True)
    device_serial = models.CharField(max_length=80, blank=True)
    is_active = models.BooleanField(default=True)
    registered_at = models.DateTimeField(null=True, blank=True)
    last_fiscal_day_no = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["institution"]

    def __str__(self):
        return self.device_id or self.device_serial or f"device {self.pk}"

    @classmethod
    def for_institution(cls, institution):
        device, _created = cls.all_objects.get_or_create(
            institution=institution,
            defaults={
                "device_id": settings.ZIMRA_DEVICE_ID,
                "device_serial": settings.ZIMRA_DEVICE_SERIAL,
            })
        return device


class FiscalDay(TenantModel):
    """One open fiscal day. Documents are grouped and numbered inside it."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    day = models.DateField(default=timezone.localdate)
    fiscal_day_no = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=10, choices=Status.choices,
                              default=Status.OPEN)
    opened_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(null=True, blank=True)
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL,
                                  related_name="fiscal_days_opened")
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL,
                                  related_name="fiscal_days_closed")

    class Meta:
        ordering = ["-day", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["institution", "day"],
                                    name="uniq_fiscal_day")
        ]

    def __str__(self):
        return f"Fiscal day {self.fiscal_day_no} ({self.day}) {self.status}"


class FiscalSubmission(TenantModel):
    """What was sent to ZIMRA for one issued document, and the reply."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUBMITTED = "submitted", "Submitted"
        VERIFIED = "verified", "Verified"
        FAILED = "failed", "Failed"

    document_type = models.CharField(max_length=20, choices=DocumentType.choices)
    document_id = models.PositiveIntegerField()
    document_number = models.CharField(max_length=40, blank=True)
    fiscal_day = models.ForeignKey(FiscalDay, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name="submissions")
    receipt_counter = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=12, choices=Status.choices,
                              default=Status.PENDING)
    verification_code = models.CharField(max_length=120, blank=True)
    qr_data = models.TextField(blank=True)
    # The masthead as it was when the document was issued. A fiscalised
    # document is reprinted with this frozen copy, never with a later logo or
    # address, so the paper copy always matches what ZIMRA was told.
    layout = models.JSONField(default=dict, blank=True)
    response = models.JSONField(default=dict, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "document_type", "document_id"],
                name="uniq_fiscal_submission")
        ]

    def __str__(self):
        return f"{self.get_document_type_display()} {self.document_number} {self.status}"

    @property
    def is_verified(self):
        return self.status == self.Status.VERIFIED
