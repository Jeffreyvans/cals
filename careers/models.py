"""Careers, employers, placements, attachment logs and grading."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.models import TenantModel


class Employer(TenantModel):
    name = models.CharField(max_length=160)
    sector = models.CharField(max_length=80, blank=True)
    contact_name = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Placement(TenantModel):
    class Status(models.TextChoices):
        PROPOSED = "proposed", "Proposed"
        APPROVED = "approved", "Approved"
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    student = models.ForeignKey(
        "academics.Student", on_delete=models.CASCADE, related_name="placements"
    )
    employer = models.ForeignKey(Employer, on_delete=models.PROTECT, related_name="placements")
    title = models.CharField(max_length=150)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    supervisor = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROPOSED)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-start_date", "-pk"]

    def clean(self):
        super().clean()
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "The placement cannot end before it starts."})

    def __str__(self):
        return f"{self.student} at {self.employer}"


class AttachmentLog(TenantModel):
    placement = models.ForeignKey(
        Placement, on_delete=models.CASCADE, related_name="logs"
    )
    date = models.DateField(default=timezone.localdate)
    hours = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    activity = models.TextField()
    supervisor_note = models.TextField(blank=True)
    signed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="attachment_logs_signed",
    )

    class Meta:
        ordering = ["-date", "-pk"]

    def __str__(self):
        return f"{self.placement} - {self.date}"


class SupervisorReport(TenantModel):
    """An employer supervisor's written assessment of an attachment."""

    placement = models.ForeignKey(
        Placement, on_delete=models.CASCADE, related_name="supervisor_reports"
    )
    date = models.DateField(default=timezone.localdate)
    supervisor_name = models.CharField(max_length=120)
    rating = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        choices=[(i, str(i)) for i in range(1, 6)],
        help_text="Overall rating out of five.",
    )
    strengths = models.TextField(blank=True)
    concerns = models.TextField(blank=True)
    signed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="supervisor_reports_signed",
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-pk"]

    def __str__(self):
        return f"{self.placement} report {self.date}"


class AttachmentGrade(TenantModel):
    """The final mark for a placement, with a letter derived from the mark."""

    placement = models.OneToOneField(
        Placement, on_delete=models.CASCADE, related_name="grade"
    )
    mark = models.DecimalField(max_digits=5, decimal_places=2)
    comments = models.TextField(blank=True)
    graded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="attachment_grades_given",
    )
    graded_on = models.DateField(default=timezone.localdate)

    class Meta:
        ordering = ["-graded_on", "-pk"]

    def clean(self):
        super().clean()
        if self.mark is not None and not (0 <= self.mark <= 100):
            raise ValidationError({"mark": "The mark must be between 0 and 100."})

    @property
    def letter(self):
        mark = self.mark or 0
        if mark >= 75:
            return "A"
        if mark >= 65:
            return "B"
        if mark >= 55:
            return "C"
        if mark >= 50:
            return "D"
        return "F"

    def __str__(self):
        return f"{self.placement} - {self.mark} ({self.letter})"


class CareerEvent(TenantModel):
    title = models.CharField(max_length=150)
    date = models.DateField()
    venue = models.CharField(max_length=120, blank=True)
    description = models.TextField(blank=True)
    organiser = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="career_events_organised",
    )

    class Meta:
        ordering = ["date", "title"]

    def __str__(self):
        return f"{self.title} ({self.date})"
