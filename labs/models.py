"""Laboratories, equipment, safety, consumables and practicals."""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from core.models import TenantModel


class Lab(TenantModel):
    name = models.CharField(max_length=120)
    location = models.CharField(max_length=120, blank=True)
    capacity = models.PositiveIntegerField(default=0)
    technician = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="labs_managed",
    )
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Equipment(TenantModel):
    class Status(models.TextChoices):
        AVAILABLE = "available", "Available"
        IN_USE = "in_use", "In use"
        MAINTENANCE = "maintenance", "Under maintenance"
        BROKEN = "broken", "Broken"
        RETIRED = "retired", "Retired"

    name = models.CharField(max_length=150)
    code = models.CharField(max_length=40, blank=True)
    lab = models.ForeignKey(
        Lab, null=True, blank=True, on_delete=models.SET_NULL, related_name="equipment"
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.AVAILABLE)
    purchased_on = models.DateField(null=True, blank=True)
    last_serviced = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "equipment"

    def __str__(self):
        return f"{self.name} ({self.code})" if self.code else self.name


class EquipmentBooking(TenantModel):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        APPROVED = "approved", "Approved"
        IN_USE = "in_use", "In use"
        RETURNED = "returned", "Returned"
        CANCELLED = "cancelled", "Cancelled"

    equipment = models.ForeignKey(
        Equipment, on_delete=models.CASCADE, related_name="bookings"
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="equipment_bookings",
    )
    purpose = models.CharField(max_length=200)
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REQUESTED)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="equipment_bookings_approved",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-starts_at", "-pk"]

    def clean(self):
        super().clean()
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "The booking must end after it starts."})

    def __str__(self):
        return f"{self.equipment} - {self.purpose}"


class SafetyInduction(TenantModel):
    """A student's safety induction for a laboratory, with an expiry date."""

    lab = models.ForeignKey(Lab, on_delete=models.CASCADE, related_name="inductions")
    student = models.ForeignKey(
        "academics.Student", on_delete=models.CASCADE, related_name="lab_inductions"
    )
    completed_on = models.DateField(default=timezone.localdate)
    expires_on = models.DateField(null=True, blank=True)
    passed = models.BooleanField(default=True)
    inducted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lab_inductions_given",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-completed_on", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "lab", "student"],
                name="uniq_lab_induction_student",
            )
        ]

    @property
    def is_valid(self):
        return self.passed and (
            self.expires_on is None or self.expires_on >= timezone.localdate()
        )

    def __str__(self):
        return f"{self.student} - {self.lab}"


class Consumable(TenantModel):
    """A laboratory consumable tracked by quantity on hand."""

    lab = models.ForeignKey(
        Lab, null=True, blank=True, on_delete=models.SET_NULL, related_name="consumables"
    )
    name = models.CharField(max_length=150)
    unit = models.CharField(max_length=20, default="unit")
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal("0"))],
    )
    reorder_level = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal("0"))],
    )
    unit_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal("0"))],
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "name"], name="uniq_consumable_name"
            )
        ]

    @property
    def needs_reorder(self):
        return self.quantity <= self.reorder_level

    def __str__(self):
        return f"{self.name} ({self.quantity} {self.unit})"


class ConsumableIssue(TenantModel):
    """Consumables taken out of the store for a student's practical."""

    consumable = models.ForeignKey(
        Consumable, on_delete=models.CASCADE, related_name="issues"
    )
    student = models.ForeignKey(
        "academics.Student", on_delete=models.CASCADE, related_name="consumable_issues"
    )
    date = models.DateField(default=timezone.localdate)
    quantity = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="consumables_issued",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-date", "-pk"]

    def clean(self):
        super().clean()
        if (
            self.consumable_id
            and self.quantity
            and self._state.adding
            and self.quantity > self.consumable.quantity
        ):
            raise ValidationError(
                {"quantity": "There is not enough stock for this issue."}
            )

    def save(self, *a, **kw):
        adding = self._state.adding
        if adding:
            self.full_clean()
        super().save(*a, **kw)
        if adding:
            Consumable.all_objects.filter(pk=self.consumable_id).update(
                quantity=models.F("quantity") - self.quantity
            )

    def __str__(self):
        return f"{self.consumable} - {self.student}"


class PracticalAssessment(TenantModel):
    """A mark for a student's laboratory practical."""

    lab = models.ForeignKey(
        Lab, null=True, blank=True, on_delete=models.SET_NULL, related_name="practicals"
    )
    student = models.ForeignKey(
        "academics.Student", on_delete=models.CASCADE, related_name="practical_assessments"
    )
    title = models.CharField(max_length=150)
    date = models.DateField(default=timezone.localdate)
    mark = models.DecimalField(
        max_digits=6, decimal_places=2, validators=[MinValueValidator(Decimal("0"))]
    )
    max_mark = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=100,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    assessor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="practicals_assessed",
    )
    comments = models.TextField(blank=True)

    class Meta:
        ordering = ["-date", "-pk"]

    def clean(self):
        super().clean()
        if self.mark is not None and self.max_mark and self.mark > self.max_mark:
            raise ValidationError({"mark": "The mark cannot exceed the maximum mark."})

    @property
    def percentage(self):
        if not self.max_mark:
            return Decimal("0")
        return (self.mark / self.max_mark * 100).quantize(Decimal("0.1"))

    def __str__(self):
        return f"{self.student} - {self.title}"


class MaintenanceLog(TenantModel):
    equipment = models.ForeignKey(
        Equipment, on_delete=models.CASCADE, related_name="maintenance_logs"
    )
    date = models.DateField(default=timezone.localdate)
    description = models.TextField()
    cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    performed_by = models.CharField(max_length=120, blank=True)
    next_service = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-date", "-pk"]

    def __str__(self):
        return f"{self.equipment} - {self.date}"
