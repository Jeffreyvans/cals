"""Examination sittings, papers, candidates and marks."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from core.models import TenantModel


class Exam(TenantModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SCHEDULED = "scheduled", "Scheduled"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        PUBLISHED = "published", "Results published"
        CANCELLED = "cancelled", "Cancelled"

    name = models.CharField(max_length=150)
    term = models.ForeignKey(
        "academics.Term", on_delete=models.PROTECT, related_name="exam_sessions"
    )
    starts_on = models.DateField()
    ends_on = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-starts_on", "-pk"]

    def clean(self):
        super().clean()
        if self.ends_on and self.starts_on and self.ends_on < self.starts_on:
            raise ValidationError({"ends_on": "The sitting cannot end before it starts."})

    def __str__(self):
        return f"{self.name} ({self.term})"


class ExamPaper(TenantModel):
    class Session(models.TextChoices):
        MORNING = "morning", "Morning"
        AFTERNOON = "afternoon", "Afternoon"
        EVENING = "evening", "Evening"

    class MarkStatus(models.TextChoices):
        ENTERING = "entering", "Marks being entered"
        SUBMITTED = "submitted", "Submitted for moderation"
        MODERATED = "moderated", "Moderated"
        RELEASED = "released", "Released"

    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="papers")
    subject = models.ForeignKey(
        "academics.Subject", null=True, blank=True, on_delete=models.PROTECT, related_name="exam_papers"
    )
    course = models.ForeignKey(
        "academics.Course", null=True, blank=True, on_delete=models.PROTECT, related_name="exam_papers"
    )
    date = models.DateField()
    session = models.CharField(max_length=20, choices=Session.choices, default=Session.MORNING)
    start_time = models.TimeField()
    duration_minutes = models.PositiveIntegerField(default=120)
    venue = models.CharField(max_length=120, blank=True)
    invigilator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="exam_papers_invigilated",
    )
    mark_status = models.CharField(
        max_length=20, choices=MarkStatus.choices, default=MarkStatus.ENTERING
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="exam_papers_submitted",
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    moderated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="exam_papers_moderated",
    )
    moderated_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="exam_papers_released",
    )
    released_at = models.DateTimeField(null=True, blank=True)
    moderation_note = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["date", "start_time", "pk"]

    def clean(self):
        super().clean()
        if bool(self.subject_id) == bool(self.course_id):
            raise ValidationError("Choose either a subject or a course for this paper.")
        if self.exam_id and self.date and self.start_time:
            from .services import schedule_issues

            issues = schedule_issues(self)
            if issues:
                raise ValidationError([issue["message"] for issue in issues])

    def __str__(self):
        return f"{self.exam} - {self.subject or self.course}"


class ExamCandidate(TenantModel):
    paper = models.ForeignKey(ExamPaper, on_delete=models.CASCADE, related_name="candidates")
    student = models.ForeignKey(
        "academics.Student", on_delete=models.CASCADE, related_name="exam_candidates"
    )
    candidate_no = models.CharField(max_length=30, blank=True)
    seat_no = models.CharField(max_length=20, blank=True)
    mark = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    grade = models.CharField(max_length=10, blank=True)
    published = models.BooleanField(default=False)

    class Meta:
        ordering = ["paper__date", "candidate_no", "student__user__last_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "paper", "student"], name="uniq_exam_candidate"
            )
        ]

    def clean(self):
        super().clean()
        if self.paper_id and self.student_id:
            from .services import student_paper_clashes

            clashes = student_paper_clashes(
                self.paper, self.student, exclude_pk=self.pk
            )
            if clashes:
                other = clashes[0]
                raise ValidationError(
                    f"{self.student} is already sitting {other.paper} "
                    f"at {other.paper.start_time:%H:%M} on {other.paper.date}."
                )
        if self.paper_id and self.paper.mark_status in (
            ExamPaper.MarkStatus.MODERATED,
            ExamPaper.MarkStatus.RELEASED,
        ):
            if self.pk is None:
                raise ValidationError(
                    "This paper's marks are locked; reopen it before adding a candidate."
                )
            previous = (
                ExamCandidate.all_objects.filter(pk=self.pk)
                .values("mark", "grade")
                .first()
            )
            if previous and (
                previous["mark"] != self.mark or previous["grade"] != self.grade
            ):
                raise ValidationError(
                    "Marks are locked after moderation; reopen the paper to change them."
                )

    def __str__(self):
        return f"{self.student} in {self.paper}"


class InvigilationDuty(TenantModel):
    paper = models.ForeignKey(ExamPaper, on_delete=models.CASCADE, related_name="duties")
    invigilator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="invigilation_duties"
    )
    room = models.CharField(max_length=120, blank=True)
    confirmed = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["paper__date", "paper__start_time"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "paper", "invigilator"], name="uniq_invigilation_duty"
            )
        ]

    def __str__(self):
        return f"{self.invigilator} at {self.paper}"
