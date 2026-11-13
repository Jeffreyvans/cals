"""Sports teams, fixtures, equipment loans and house points."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.models import TenantModel


class Team(TenantModel):
    name = models.CharField(max_length=120)
    sport = models.CharField(max_length=80)
    age_group = models.CharField(max_length=40, blank=True)
    coach = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="teams_coached",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sport", "name"]

    def __str__(self):
        return f"{self.name} ({self.sport})"


class TeamMember(TenantModel):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="members")
    student = models.ForeignKey(
        "academics.Student", on_delete=models.CASCADE, related_name="team_memberships"
    )
    position = models.CharField(max_length=60, blank=True)
    joined_on = models.DateField(default=timezone.localdate)

    class Meta:
        ordering = ["team__name", "student__user__last_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "team", "student"], name="uniq_team_member"
            )
        ]

    def __str__(self):
        return f"{self.student} in {self.team}"


class Fixture(TenantModel):
    class Result(models.TextChoices):
        PENDING = "pending", "Not played"
        WIN = "win", "Win"
        DRAW = "draw", "Draw"
        LOSS = "loss", "Loss"
        CANCELLED = "cancelled", "Cancelled"

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="fixtures")
    opponent = models.CharField(max_length=120)
    date = models.DateField()
    venue = models.CharField(max_length=120, blank=True)
    home = models.BooleanField(default=True)
    score_for = models.PositiveIntegerField(default=0)
    score_against = models.PositiveIntegerField(default=0)
    result = models.CharField(max_length=20, choices=Result.choices, default=Result.PENDING)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-date", "-pk"]

    def __str__(self):
        return f"{self.team} vs {self.opponent}"


class Equipment(TenantModel):
    """Sports equipment that can be issued to students and returned."""

    institution = models.ForeignKey(
        "core.Institution",
        on_delete=models.CASCADE,
        related_name="sports_equipment_records",
    )

    class Condition(models.TextChoices):
        GOOD = "good", "Good"
        FAIR = "fair", "Fair"
        POOR = "poor", "Needs repair"
        RETIRED = "retired", "Retired"

    name = models.CharField(max_length=150)
    sport = models.CharField(max_length=80, blank=True)
    code = models.CharField(max_length=40, blank=True)
    total = models.PositiveIntegerField(default=1)
    available = models.PositiveIntegerField(default=1)
    condition = models.CharField(
        max_length=20, choices=Condition.choices, default=Condition.GOOD
    )
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["sport", "name"]
        verbose_name_plural = "equipment"

    def clean(self):
        super().clean()
        if self.available > self.total:
            raise ValidationError(
                {"available": "Available items cannot exceed the total stock."}
            )

    def __str__(self):
        return f"{self.name} ({self.code})" if self.code else self.name


class EquipmentLoan(TenantModel):
    class Status(models.TextChoices):
        BORROWED = "borrowed", "On loan"
        RETURNED = "returned", "Returned"
        OVERDUE = "overdue", "Overdue"
        LOST = "lost", "Lost"

    equipment = models.ForeignKey(
        Equipment, on_delete=models.PROTECT, related_name="loans"
    )
    student = models.ForeignKey(
        "academics.Student", on_delete=models.CASCADE, related_name="sports_equipment_loans"
    )
    borrowed_on = models.DateField(default=timezone.localdate)
    due_on = models.DateField()
    returned_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.BORROWED)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sports_equipment_issued",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-borrowed_on", "-pk"]

    def clean(self):
        super().clean()
        if self.due_on and self.borrowed_on and self.due_on < self.borrowed_on:
            raise ValidationError({"due_on": "The due date cannot be before the loan date."})
        if self.equipment_id and self._state.adding and not self.returned_on:
            if self.equipment.available < 1:
                raise ValidationError({"equipment": "No copies of this equipment are available."})

    @property
    def is_overdue(self):
        return self.status in (self.Status.BORROWED, self.Status.OVERDUE) and (
            self.due_on < timezone.localdate()
        )

    def save(self, *a, **kw):
        adding = self._state.adding
        old_returned = None
        if not adding:
            old_returned = (
                EquipmentLoan.all_objects.filter(pk=self.pk)
                .values_list("returned_on", flat=True)
                .first()
            )
        super().save(*a, **kw)
        if adding and self.equipment_id and not self.returned_on:
            Equipment.all_objects.filter(pk=self.equipment_id).update(
                available=models.F("available") - 1
            )
        if not adding and old_returned is None and self.returned_on:
            Equipment.all_objects.filter(pk=self.equipment_id).update(
                available=models.F("available") + 1
            )

    def __str__(self):
        return f"{self.equipment} - {self.student}"


class HousePoint(TenantModel):
    house = models.CharField(max_length=60)
    student = models.ForeignKey(
        "academics.Student",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="house_points",
    )
    points = models.IntegerField(default=0)
    reason = models.CharField(max_length=200)
    awarded_on = models.DateField(default=timezone.localdate)
    awarded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="house_points_awarded",
    )

    class Meta:
        ordering = ["-awarded_on", "-pk"]

    def __str__(self):
        return f"{self.house}: {self.points} points"
