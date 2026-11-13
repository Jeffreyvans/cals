"""Transport routes, vehicles, stops, student assignments and fuel logs."""

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from core.models import TenantModel


class Route(TenantModel):
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=30, blank=True)
    description = models.TextField(blank=True)
    fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "name"], name="uniq_transport_route_name"
            )
        ]

    def __str__(self):
        return f"{self.code} {self.name}" if self.code else self.name


class Vehicle(TenantModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        MAINTENANCE = "maintenance", "Under maintenance"
        RETIRED = "retired", "Retired"

    registration = models.CharField(max_length=30)
    capacity = models.PositiveIntegerField(default=0)
    driver_name = models.CharField(max_length=120, blank=True)
    driver_phone = models.CharField(max_length=30, blank=True)
    conductor = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["registration"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "registration"], name="uniq_transport_registration"
            )
        ]

    def __str__(self):
        return self.registration


class Stop(TenantModel):
    route = models.ForeignKey(Route, on_delete=models.CASCADE, related_name="stops")
    name = models.CharField(max_length=120)
    pickup_time = models.TimeField(null=True, blank=True)
    dropoff_time = models.TimeField(null=True, blank=True)
    order = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["route__name", "order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "route", "name"], name="uniq_route_stop"
            )
        ]

    def __str__(self):
        return f"{self.route} - {self.name}"


class TransportAssignment(TenantModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        ENDED = "ended", "Ended"

    student = models.ForeignKey(
        "academics.Student", on_delete=models.CASCADE, related_name="transport_assignments"
    )
    route = models.ForeignKey(Route, on_delete=models.PROTECT, related_name="assignments")
    stop = models.ForeignKey(
        Stop, null=True, blank=True, on_delete=models.SET_NULL, related_name="assignments"
    )
    vehicle = models.ForeignKey(
        Vehicle, null=True, blank=True, on_delete=models.SET_NULL, related_name="assignments"
    )
    term = models.ForeignKey(
        "academics.Term", null=True, blank=True, on_delete=models.SET_NULL, related_name="transport_assignments"
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    fee_invoice = models.ForeignKey(
        "finance.Invoice",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="transport_assignments",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["route__name", "student__user__last_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "student", "route", "term"],
                name="uniq_transport_assignment",
            )
        ]

    def clean(self):
        super().clean()
        if self.stop_id and self.route_id and self.stop.route_id != self.route_id:
            raise ValidationError({"stop": "That stop belongs to another route."})

    def __str__(self):
        return f"{self.student} on {self.route}"


class FuelLog(TenantModel):
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="fuel_logs")
    date = models.DateField(default=timezone.localdate)
    litres = models.DecimalField(
        max_digits=8, decimal_places=2, validators=[MinValueValidator(0)]
    )
    cost = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(0)]
    )
    odometer = models.PositiveIntegerField(null=True, blank=True)
    driver = models.CharField(max_length=120, blank=True)
    reference = models.CharField(max_length=60, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-date", "-pk"]
        verbose_name = "fuel log"
        verbose_name_plural = "fuel logs"

    def __str__(self):
        return f"{self.vehicle} {self.date} {self.litres}L"


class TripLog(TenantModel):
    class Kind(models.TextChoices):
        MORNING = "morning", "Morning pickup"
        AFTERNOON = "afternoon", "Afternoon drop-off"
        EXTRA = "extra", "Extra trip"

    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="trips")
    route = models.ForeignKey(Route, on_delete=models.CASCADE, related_name="trips")
    date = models.DateField(default=timezone.localdate)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.MORNING)
    departed_at = models.TimeField(null=True, blank=True)
    returned_at = models.TimeField(null=True, blank=True)
    passengers = models.PositiveIntegerField(default=0)
    driver = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-date", "-pk"]

    def __str__(self):
        return f"{self.route} {self.date} ({self.get_kind_display()})"
