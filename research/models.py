"""Research projects, milestones, theses, supervision, ethics and publications."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.models import TenantModel


class ResearchProject(TenantModel):
    class Status(models.TextChoices):
        PROPOSED = "proposed", "Proposed"
        ACTIVE = "active", "Active"
        ON_HOLD = "on_hold", "On hold"
        COMPLETED = "completed", "Completed"
        ABANDONED = "abandoned", "Abandoned"

    title = models.CharField(max_length=200)
    student = models.ForeignKey(
        "academics.Student", on_delete=models.CASCADE, related_name="research_projects"
    )
    supervisor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="research_projects_supervised",
    )
    start_date = models.DateField(default=timezone.localdate)
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROPOSED)
    abstract = models.TextField(blank=True)
    keywords = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-start_date", "-pk"]

    def clean(self):
        super().clean()
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "The project cannot end before it starts."})

    def __str__(self):
        return self.title


class Milestone(TenantModel):
    """A dated checkpoint on the way to finishing a research project."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"

    project = models.ForeignKey(
        ResearchProject, on_delete=models.CASCADE, related_name="milestones"
    )
    title = models.CharField(max_length=150)
    due_date = models.DateField()
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    completed_on = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["due_date", "pk"]

    @property
    def is_overdue(self):
        return (
            self.status != self.Status.COMPLETED
            and self.due_date < timezone.localdate()
        )

    def __str__(self):
        return f"{self.project} - {self.title}"


class Thesis(TenantModel):
    """The final written thesis that a project produces."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        UNDER_REVIEW = "under_review", "Under review"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"

    project = models.OneToOneField(
        ResearchProject, on_delete=models.CASCADE, related_name="thesis"
    )
    title = models.CharField(max_length=250)
    abstract = models.TextField(blank=True)
    pages = models.PositiveIntegerField(null=True, blank=True)
    submitted_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)

    class Meta:
        ordering = ["-submitted_on", "-pk"]
        verbose_name_plural = "theses"

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"


class SupervisionMeeting(TenantModel):
    project = models.ForeignKey(
        ResearchProject, on_delete=models.CASCADE, related_name="meetings"
    )
    date = models.DateField(default=timezone.localdate)
    agenda = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    next_meeting = models.DateField(null=True, blank=True)
    attended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="supervision_meetings_attended",
    )

    class Meta:
        ordering = ["-date", "-pk"]

    def __str__(self):
        return f"{self.project} - {self.date}"


class EthicsApplication(TenantModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        EXPIRED = "expired", "Expired"

    project = models.ForeignKey(
        ResearchProject, on_delete=models.CASCADE, related_name="ethics_applications"
    )
    reference = models.CharField(max_length=40, blank=True)
    submitted_on = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    approved_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-submitted_on", "-pk"]
        verbose_name = "ethics application"
        verbose_name_plural = "ethics applications"

    def __str__(self):
        return f"{self.project} ethics"


class Publication(TenantModel):
    class Kind(models.TextChoices):
        JOURNAL = "journal", "Journal article"
        CONFERENCE = "conference", "Conference paper"
        BOOK = "book", "Book / chapter"
        THESIS = "thesis", "Thesis"
        OTHER = "other", "Other"

    project = models.ForeignKey(
        ResearchProject, on_delete=models.CASCADE, related_name="publications"
    )
    title = models.CharField(max_length=250)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.JOURNAL)
    venue = models.CharField(max_length=200, blank=True)
    published_on = models.DateField(null=True, blank=True)
    url = models.URLField(blank=True)
    doi = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-published_on", "title"]

    def __str__(self):
        return self.title
