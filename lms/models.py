from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from core.models import TenantModel


def _zero():
    return Decimal("0")


class CourseSite(TenantModel):
    course = models.OneToOneField(
        "academics.Course", on_delete=models.CASCADE, related_name="lms_site")
    title = models.CharField(max_length=150, blank=True)
    description = models.TextField(blank=True)
    welcome = models.TextField(blank=True)
    is_published = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="lms_sites_created")
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["course__code"]

    @property
    def display_title(self):
        return self.title or str(self.course)

    def __str__(self):
        return self.display_title


class SiteSection(TenantModel):
    site = models.ForeignKey(CourseSite, on_delete=models.CASCADE, related_name="sections")
    title = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    order = models.PositiveSmallIntegerField(default=0)
    visible_from = models.DateField(null=True, blank=True)
    visible_until = models.DateField(null=True, blank=True)
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "pk"]

    def clean(self):
        super().clean()
        if self.visible_from and self.visible_until and self.visible_until < self.visible_from:
            raise ValidationError({"visible_until": "Visibility cannot end before it starts."})

    @property
    def is_visible(self):
        today = timezone.localdate()
        if not self.is_published:
            return False
        if self.visible_from and today < self.visible_from:
            return False
        return not (self.visible_until and today > self.visible_until)

    def __str__(self):
        return self.title


class ContentItem(TenantModel):
    class Kind(models.TextChoices):
        NOTE = "note", "Note"
        FILE = "file", "File"
        LINK = "link", "Link"
        VIDEO = "video", "Video link"

    section = models.ForeignKey(SiteSection, on_delete=models.CASCADE, related_name="items")
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.NOTE)
    title = models.CharField(max_length=150)
    body = models.TextField(blank=True)
    file = models.FileField(upload_to="lms/content/", blank=True)
    url = models.URLField(blank=True)
    order = models.PositiveSmallIntegerField(default=0)
    visible_from = models.DateField(null=True, blank=True)
    visible_until = models.DateField(null=True, blank=True)
    is_published = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="lms_content_created")

    class Meta:
        ordering = ["order", "pk"]

    def clean(self):
        super().clean()
        if self.visible_from and self.visible_until and self.visible_until < self.visible_from:
            raise ValidationError({"visible_until": "Visibility cannot end before it starts."})
        if self.kind == self.Kind.FILE and not self.file:
            raise ValidationError({"file": "Choose the file to share."})
        if self.kind in (self.Kind.LINK, self.Kind.VIDEO) and not self.url:
            raise ValidationError({"url": "Paste the address to open."})

    @property
    def is_visible(self):
        today = timezone.localdate()
        if not self.is_published:
            return False
        if self.visible_from and today < self.visible_from:
            return False
        return not (self.visible_until and today > self.visible_until)

    @property
    def target_url(self):
        return self.file.url if self.file else self.url

    def __str__(self):
        return self.title


class Assignment(TenantModel):
    class LatePolicy(models.TextChoices):
        NONE = "none", "Accept late work without penalty"
        PENALTY = "penalty", "Apply a late penalty"
        BLOCK = "block", "Do not accept late work"

    site = models.ForeignKey(CourseSite, on_delete=models.CASCADE, related_name="assignments")
    title = models.CharField(max_length=150)
    instructions = models.TextField(blank=True)
    due_at = models.DateTimeField()
    late_policy = models.CharField(
        max_length=10, choices=LatePolicy.choices, default=LatePolicy.NONE)
    late_penalty_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)])
    max_score = models.DecimalField(
        max_digits=7, decimal_places=2, default=100,
        validators=[MinValueValidator(Decimal("0.01"))])
    attachment = models.FileField(upload_to="lms/assignments/", blank=True)
    allow_resubmission = models.BooleanField(default=True)
    is_published = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="lms_assignments_created")
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["due_at", "pk"]

    def clean(self):
        super().clean()
        if self.late_policy == self.LatePolicy.PENALTY and not self.late_penalty_percent:
            raise ValidationError(
                {"late_penalty_percent": "Give the percentage to deduct for late work."})
        if self.max_score is not None and self.max_score <= 0:
            raise ValidationError({"max_score": "The maximum score must be above zero."})

    def is_late(self, at=None):
        return (at or timezone.now()) > self.due_at

    def accepts_submission(self, at=None):
        if not self.is_published:
            return False
        if self.late_policy == self.LatePolicy.BLOCK and self.is_late(at):
            return False
        return True

    def apply_late_penalty(self, mark, at=None):
        if mark is None:
            return None
        if self.late_policy != self.LatePolicy.PENALTY or not self.is_late(at):
            return mark
        factor = Decimal("1") - (self.late_penalty_percent / Decimal("100"))
        return (Decimal(mark) * factor).quantize(Decimal("0.01"))

    def __str__(self):
        return self.title


class Rubric(TenantModel):
    assignment = models.OneToOneField(
        Assignment, on_delete=models.CASCADE, related_name="rubric")
    title = models.CharField(max_length=150, default="Marking rubric")
    description = models.TextField(blank=True)

    def __str__(self):
        return f"{self.assignment}: {self.title}"


class RubricCriterion(TenantModel):
    rubric = models.ForeignKey(Rubric, on_delete=models.CASCADE, related_name="criteria")
    description = models.CharField(max_length=200)
    max_points = models.DecimalField(
        max_digits=6, decimal_places=2, default=10,
        validators=[MinValueValidator(Decimal("0.01"))])
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "pk"]

    def __str__(self):
        return f"{self.description} ({self.max_points:g})"


class Submission(TenantModel):
    assignment = models.ForeignKey(
        Assignment, on_delete=models.CASCADE, related_name="submissions")
    student = models.ForeignKey(
        "academics.Student", on_delete=models.CASCADE, related_name="lms_submissions")
    text = models.TextField(blank=True)
    file = models.FileField(upload_to="lms/submissions/", blank=True)
    submitted_at = models.DateTimeField(default=timezone.now)
    attempt_count = models.PositiveSmallIntegerField(default=1)
    is_late = models.BooleanField(default=False)
    feedback = models.TextField(blank=True)
    mark = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)])
    graded_at = models.DateTimeField(null=True, blank=True)
    graded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="lms_submissions_graded")
    plagiarism_score = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True)
    plagiarism_checked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-submitted_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["assignment", "student"], name="uniq_lms_submission"),
        ]

    def clean(self):
        super().clean()
        if not self.text.strip() and not self.file:
            raise ValidationError("Write an answer or attach a file.")

    def save(self, *a, **kw):
        if self.assignment_id and self.submitted_at:
            self.is_late = self.assignment.is_late(self.submitted_at)
        super().save(*a, **kw)

    @property
    def is_graded(self):
        return self.mark is not None

    def __str__(self):
        return f"{self.student} – {self.assignment}"


class CriterionScore(TenantModel):
    submission = models.ForeignKey(
        Submission, on_delete=models.CASCADE, related_name="criterion_scores")
    criterion = models.ForeignKey(
        RubricCriterion, on_delete=models.CASCADE, related_name="scores")
    points = models.DecimalField(
        max_digits=6, decimal_places=2, default=0,
        validators=[MinValueValidator(0)])
    comment = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["submission", "criterion"], name="uniq_lms_criterion_score"),
        ]

    def clean(self):
        super().clean()
        if self.criterion_id and self.points is not None \
                and self.points > self.criterion.max_points:
            raise ValidationError({"points": "That is more than the criterion is worth."})

    def __str__(self):
        return f"{self.criterion}: {self.points}"


class Quiz(TenantModel):
    site = models.ForeignKey(CourseSite, on_delete=models.CASCADE, related_name="quizzes")
    title = models.CharField(max_length=150)
    instructions = models.TextField(blank=True)
    time_limit_minutes = models.PositiveSmallIntegerField(
        default=0, help_text="0 means no time limit.")
    max_attempts = models.PositiveSmallIntegerField(default=1)
    randomize_questions = models.BooleanField(default=True)
    randomize_choices = models.BooleanField(default=False)
    opens_at = models.DateTimeField(null=True, blank=True)
    closes_at = models.DateTimeField(null=True, blank=True)
    is_published = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="lms_quizzes_created")
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created", "pk"]

    def clean(self):
        super().clean()
        if self.opens_at and self.closes_at and self.closes_at <= self.opens_at:
            raise ValidationError({"closes_at": "The quiz must close after it opens."})

    @property
    def total_points(self):
        return self.questions.aggregate(t=models.Sum("points"))["t"] or _zero()

    @property
    def duration(self):
        if not self.time_limit_minutes:
            return None
        return timedelta(minutes=self.time_limit_minutes)

    def is_open(self, at=None):
        at = at or timezone.now()
        if not self.is_published:
            return False
        if self.opens_at and at < self.opens_at:
            return False
        return not (self.closes_at and at > self.closes_at)

    def attempts_left(self, student):
        used = self.attempts.filter(student=student).exclude(
            status=QuizAttempt.Status.IN_PROGRESS).count()
        return max(self.max_attempts - used, 0)

    def __str__(self):
        return self.title


class Question(TenantModel):
    class Kind(models.TextChoices):
        MULTIPLE_CHOICE = "multiple_choice", "Multiple choice"
        TRUE_FALSE = "true_false", "True / false"
        SHORT_ANSWER = "short_answer", "Short answer"
        MATCHING = "matching", "Matching"

    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="questions")
    kind = models.CharField(
        max_length=20, choices=Kind.choices, default=Kind.MULTIPLE_CHOICE)
    text = models.TextField()
    points = models.DecimalField(
        max_digits=6, decimal_places=2, default=1,
        validators=[MinValueValidator(Decimal("0.01"))])
    order = models.PositiveSmallIntegerField(default=0)
    accepted_answers = models.TextField(
        blank=True, help_text="For short answers, put one accepted answer per line.")
    explanation = models.TextField(blank=True)

    class Meta:
        ordering = ["order", "pk"]

    @property
    def accepted_answer_list(self):
        return [line.strip().lower() for line in self.accepted_answers.splitlines()
                if line.strip()]

    def __str__(self):
        return self.text[:80]


class Choice(TenantModel):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="choices")
    text = models.CharField(max_length=300)
    is_correct = models.BooleanField(default=False)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "pk"]

    def __str__(self):
        return self.text


class MatchingPair(TenantModel):
    question = models.ForeignKey(
        Question, on_delete=models.CASCADE, related_name="matching_pairs")
    left_text = models.CharField(max_length=200)
    right_text = models.CharField(max_length=200)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "pk"]

    def __str__(self):
        return f"{self.left_text} -> {self.right_text}"


class QuizAttempt(TenantModel):
    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "In progress"
        SUBMITTED = "submitted", "Waiting for manual marking"
        GRADED = "graded", "Graded"

    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="attempts")
    student = models.ForeignKey(
        "academics.Student", on_delete=models.CASCADE, related_name="lms_quiz_attempts")
    started_at = models.DateTimeField(default=timezone.now)
    submitted_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.IN_PROGRESS)
    score = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    max_score = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    question_order = models.JSONField(default=list, blank=True)
    choice_order = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-started_at", "pk"]

    def save(self, *a, **kw):
        if self.quiz_id and not self.max_score:
            self.max_score = self.quiz.total_points
        super().save(*a, **kw)

    @property
    def is_expired(self):
        duration = self.quiz.duration
        if duration is None:
            return False
        return timezone.now() > self.started_at + duration

    @property
    def remaining_seconds(self):
        duration = self.quiz.duration
        if duration is None:
            return None
        return max(int((self.started_at + duration - timezone.now()).total_seconds()), 0)

    @property
    def ordered_questions(self):
        questions = list(self.quiz.questions.prefetch_related("choices", "matching_pairs"))
        order = [int(pk) for pk in (self.question_order or [])]
        if not order:
            return questions
        by_id = {q.pk: q for q in questions}
        return [by_id[pk] for pk in order if pk in by_id]

    def __str__(self):
        return f"{self.student} – {self.quiz}"


class QuizAnswer(TenantModel):
    attempt = models.ForeignKey(
        QuizAttempt, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="answers")
    selected_choice = models.ForeignKey(
        Choice, null=True, blank=True, on_delete=models.SET_NULL)
    text_answer = models.TextField(blank=True)
    matching_answer = models.JSONField(default=dict, blank=True)
    awarded_points = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    is_correct = models.BooleanField(default=False)
    needs_manual = models.BooleanField(default=False)
    feedback = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["attempt", "question"], name="uniq_lms_quiz_answer"),
        ]

    def __str__(self):
        return f"{self.attempt} – {self.question_id}"


class Forum(TenantModel):
    site = models.ForeignKey(CourseSite, on_delete=models.CASCADE, related_name="forums")
    title = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    is_published = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="lms_forums_created")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["title", "pk"]

    def __str__(self):
        return self.title


class ForumThread(TenantModel):
    forum = models.ForeignKey(Forum, on_delete=models.CASCADE, related_name="threads")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="lms_forum_threads")
    title = models.CharField(max_length=150)
    body = models.TextField()
    is_pinned = models.BooleanField(default=False)
    is_locked = models.BooleanField(default=False)
    is_approved = models.BooleanField(default=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_pinned", "-created", "pk"]

    @property
    def post_count(self):
        return self.posts.filter(is_approved=True).count()

    def __str__(self):
        return self.title


class ForumPost(TenantModel):
    thread = models.ForeignKey(ForumThread, on_delete=models.CASCADE, related_name="posts")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="lms_forum_posts")
    body = models.TextField()
    is_approved = models.BooleanField(default=True)
    is_flagged = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created", "pk"]

    def __str__(self):
        return f"Post by {self.author} on {self.thread}"


DiscussionForum = Forum
DiscussionThread = ForumThread
DiscussionPost = ForumPost


class LiveSession(TenantModel):
    class Provider(models.TextChoices):
        ZOOM = "zoom", "Zoom"
        GOOGLE_MEET = "google_meet", "Google Meet"
        TEAMS = "teams", "Microsoft Teams"
        OTHER = "other", "Other"

    site = models.ForeignKey(CourseSite, on_delete=models.CASCADE, related_name="live_sessions")
    title = models.CharField(max_length=150)
    provider = models.CharField(
        max_length=20, choices=Provider.choices, default=Provider.ZOOM)
    join_url = models.URLField()
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    is_published = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="lms_live_created")

    class Meta:
        ordering = ["starts_at", "pk"]

    def clean(self):
        super().clean()
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "The class must end after it starts."})

    @property
    def join_opens_at(self):
        return self.starts_at - timedelta(minutes=15)

    def is_live(self, at=None):
        at = at or timezone.now()
        return self.starts_at <= at <= self.ends_at

    def can_join(self, at=None):
        at = at or timezone.now()
        return self.join_opens_at <= at <= self.ends_at

    def __str__(self):
        return self.title


class GradeCategory(TenantModel):
    site = models.ForeignKey(
        CourseSite, on_delete=models.CASCADE, related_name="grade_categories")
    name = models.CharField(max_length=80)
    weight = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)])
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["site", "name"], name="uniq_lms_grade_category"),
        ]

    def __str__(self):
        return f"{self.name} ({self.weight:g}%)"


class GradeItem(TenantModel):
    site = models.ForeignKey(
        CourseSite, on_delete=models.CASCADE, related_name="grade_items")
    category = models.ForeignKey(
        GradeCategory, on_delete=models.CASCADE, related_name="items")
    title = models.CharField(max_length=150)
    max_score = models.DecimalField(
        max_digits=7, decimal_places=2, default=100,
        validators=[MinValueValidator(Decimal("0.01"))])
    due_date = models.DateField(null=True, blank=True)
    assignment = models.OneToOneField(
        Assignment, null=True, blank=True, on_delete=models.CASCADE,
        related_name="grade_item")
    quiz = models.OneToOneField(
        Quiz, null=True, blank=True, on_delete=models.CASCADE,
        related_name="grade_item")
    is_published = models.BooleanField(default=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["category__order", "order", "pk"]

    def clean(self):
        super().clean()
        if self.assignment_id and self.quiz_id:
            raise ValidationError("A grade item belongs to an assignment or a quiz, not both.")

    def __str__(self):
        return self.title


class GradeEntry(TenantModel):
    item = models.ForeignKey(GradeItem, on_delete=models.CASCADE, related_name="entries")
    student = models.ForeignKey(
        "academics.Student", on_delete=models.CASCADE, related_name="lms_grade_entries")
    score = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    feedback = models.TextField(blank=True)
    graded_at = models.DateTimeField(null=True, blank=True)
    graded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="lms_grade_entries_graded")

    class Meta:
        ordering = ["student__user__last_name", "student__user__first_name", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["item", "student"], name="uniq_lms_grade_entry"),
        ]

    @property
    def percentage(self):
        if self.score is None or not self.item.max_score:
            return None
        return (self.score / self.item.max_score * 100).quantize(Decimal("0.01"))

    def __str__(self):
        return f"{self.student} – {self.item}: {self.score}"


class PlagiarismReport(TenantModel):
    assignment = models.ForeignKey(
        Assignment, on_delete=models.CASCADE, related_name="plagiarism_reports")
    submission_a = models.ForeignKey(
        Submission, on_delete=models.CASCADE, related_name="plagiarism_reports_as_a")
    submission_b = models.ForeignKey(
        Submission, on_delete=models.CASCADE, related_name="plagiarism_reports_as_b")
    similarity = models.DecimalField(
        max_digits=5, decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)])
    is_flagged = models.BooleanField(default=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-similarity", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["assignment", "submission_a", "submission_b"],
                name="uniq_lms_plagiarism_pair"),
        ]

    def __str__(self):
        return f"{self.submission_a.student} / {self.submission_b.student}: {self.similarity}%"
