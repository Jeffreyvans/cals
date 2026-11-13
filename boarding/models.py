"""Boarding houses, rooms, roll call and exeats."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.models import TenantModel


class Hostel(TenantModel):
    class Gender(models.TextChoices):
        BOYS = "boys", "Boys"
        GIRLS = "girls", "Girls"
        MIXED = "mixed", "Mixed"

    name = models.CharField(max_length=120)
    gender = models.CharField(max_length=20, choices=Gender.choices, default=Gender.MIXED)
    capacity = models.PositiveIntegerField(default=0)
    matron = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="hostels_managed",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Room(TenantModel):
    hostel = models.ForeignKey(Hostel, on_delete=models.CASCADE, related_name="rooms")
    name = models.CharField(max_length=40)
    capacity = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["hostel__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "hostel", "name"], name="uniq_hostel_room"
            )
        ]

    def __str__(self):
        return f"{self.hostel} {self.name}"


class Boarder(TenantModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        LEFT = "left", "Left"
        SUSPENDED = "suspended", "Suspended"

    student = models.OneToOneField(
        "academics.Student", on_delete=models.CASCADE, related_name="boarding_record"
    )
    room = models.ForeignKey(
        Room, null=True, blank=True, on_delete=models.PROTECT, related_name="boarders"
    )
    bed = models.CharField(max_length=20, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    guardian_contact = models.CharField(max_length=120, blank=True)
    special_needs = models.TextField(blank=True)
    admitted_on = models.DateField(default=timezone.localdate)

    class Meta:
        ordering = ["room__hostel__name", "room__name", "student__user__last_name"]

    @property
    def is_allocated(self):
        return self.room_id is not None

    def __str__(self):
        return f"{self.student} in {self.room}" if self.room_id else f"{self.student} (unallocated)"


class RollCall(TenantModel):
    class Status(models.TextChoices):
        PRESENT = "present", "Present"
        ABSENT = "absent", "Absent"
        EXEAT = "exeat", "On exeat"
        SICK = "sick", "Sick bay"

    hostel = models.ForeignKey(Hostel, on_delete=models.CASCADE, related_name="roll_calls")
    date = models.DateField(default=timezone.localdate)
    boarder = models.ForeignKey(Boarder, on_delete=models.CASCADE, related_name="roll_calls")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PRESENT)
    note = models.CharField(max_length=200, blank=True)
    checked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="roll_calls_checked",
    )

    class Meta:
        ordering = ["-date", "boarder__student__user__last_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "date", "boarder"], name="uniq_roll_call"
            )
        ]
        verbose_name = "roll call"
        verbose_name_plural = "roll calls"

    def __str__(self):
        return f"{self.date} {self.boarder}: {self.get_status_display()}"


class Exeat(TenantModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Waiting for approval"
        APPROVED = "approved", "Approved"
        RETURNED = "returned", "Returned"
        REJECTED = "rejected", "Rejected"

    boarder = models.ForeignKey(Boarder, on_delete=models.CASCADE, related_name="exeats")
    reason = models.CharField(max_length=200)
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField()
    destination = models.CharField(max_length=200, blank=True)
    guardian_contact = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="exeats_approved",
    )
    returned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-starts_at", "-pk"]

    def clean(self):
        super().clean()
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "The return time must be after the start."})

    def __str__(self):
        return f"{self.boarder} - {self.reason}"
