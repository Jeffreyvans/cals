"""Student affairs: discipline, clubs and events."""

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import TenantModel


class DisciplineCase(TenantModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        HEARING = "hearing", "Hearing"
        CLOSED = "closed", "Closed"
        APPEALED = "appealed", "Appealed"

    student = models.ForeignKey(
        "academics.Student", on_delete=models.CASCADE, related_name="discipline_cases"
    )
    date = models.DateField(default=timezone.localdate)
    category = models.CharField(max_length=80)
    description = models.TextField()
    action_taken = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="discipline_cases_handled",
    )
    confidential = models.BooleanField(default=True)

    class Meta:
        ordering = ["-date", "-pk"]

    def __str__(self):
        return f"{self.student} - {self.category}"


class Club(TenantModel):
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    patron = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="clubs_patron",
    )
    meeting_day = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ClubMembership(TenantModel):
    class Role(models.TextChoices):
        MEMBER = "member", "Member"
        LEADER = "leader", "Leader"
        SECRETARY = "secretary", "Secretary"
        TREASURER = "treasurer", "Treasurer"

    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name="memberships")
    student = models.ForeignKey(
        "academics.Student", on_delete=models.CASCADE, related_name="club_memberships"
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MEMBER)
    joined_on = models.DateField(default=timezone.localdate)

    class Meta:
        ordering = ["club__name", "student__user__last_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "club", "student"], name="uniq_club_member"
            )
        ]

    def __str__(self):
        return f"{self.student} in {self.club}"


class Event(TenantModel):
    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        OPEN = "open", "Registration open"
        FULL = "full", "Full"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    title = models.CharField(max_length=150)
    date = models.DateField()
    venue = models.CharField(max_length=120, blank=True)
    description = models.TextField(blank=True)
    capacity = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED)
    organiser = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="student_events_organised",
    )

    class Meta:
        ordering = ["date", "title"]

    def __str__(self):
        return f"{self.title} ({self.date})"


class EventRegistration(TenantModel):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="registrations")
    student = models.ForeignKey(
        "academics.Student", on_delete=models.CASCADE, related_name="event_registrations"
    )
    registered_on = models.DateTimeField(default=timezone.now)
    attended = models.BooleanField(default=False)

    class Meta:
        ordering = ["event__date", "student__user__last_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "event", "student"], name="uniq_event_registration"
            )
        ]

    def __str__(self):
        return f"{self.student} at {self.event}"
