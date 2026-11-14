import uuid
from collections import Counter
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models import Q, Sum

from core.models import TenantModel

DAY_CHOICES = [(1, "Monday"), (2, "Tuesday"), (3, "Wednesday"), (4, "Thursday"),
               (5, "Friday"), (6, "Saturday"), (7, "Sunday")]

DEFAULT_GRADE_BANDS = [("A", "75", "4"), ("B", "65", "3"), ("C", "50", "2"),
                       ("D", "40", "1"), ("F", "0", "0")]

_STANDARD_AWARDS = [("Distinction", "3.50"), ("Merit", "3.00"),
                    ("Pass", "2.00"), ("Fail", "0")]
DEFAULT_AWARD_RULES = {
    "degree": [("First Class", "3.60"), ("Upper Second Class", "3.00"),
               ("Lower Second Class", "2.50"), ("Third Class", "2.00"),
               ("Fail", "0")],
    "masters": _STANDARD_AWARDS,
    "phd": [("Pass", "2.00"), ("Fail", "0")],
    "primary": _STANDARD_AWARDS,
    "secondary": _STANDARD_AWARDS,
    "short": _STANDARD_AWARDS,
    "certificate": _STANDARD_AWARDS,
    "diploma": _STANDARD_AWARDS,
    "": _STANDARD_AWARDS,
}


def balances_by_student():
    """Bulk balances: ({student_id: billed}, {paid net of refunds}, {forgiven}).

    ``forgiven`` combines waivers, discounts and applied credits so a list page
    agrees with :meth:`Student.balance`.
    """
    from finance.models import (
        CreditApplication,
        Discount,
        Invoice,
        Payment,
        Refund,
        Waiver,
    )

    billed = dict(Invoice.objects.values_list("student_id").annotate(t=Sum("amount")))
    paid = dict(Payment.objects.values_list("invoice__student_id").annotate(t=Sum("amount")))
    refunded = dict(
        Refund.objects.filter(status=Refund.Status.APPROVED)
        .values_list("payment__invoice__student_id").annotate(t=Sum("amount")))
    waived = dict(Waiver.objects.values_list("invoice__student_id").annotate(t=Sum("amount")))
    discounted = dict(
        Discount.objects.values_list("student_id").annotate(t=Sum("amount")))
    credited = dict(
        CreditApplication.objects.values_list("invoice__student_id")
        .annotate(t=Sum("amount")))
    for student_id, amount in refunded.items():
        paid[student_id] = paid.get(student_id, Decimal("0")) - amount
    for student_id, amount in discounted.items():
        waived[student_id] = waived.get(student_id, Decimal("0")) + amount
    for student_id, amount in credited.items():
        waived[student_id] = waived.get(student_id, Decimal("0")) + amount
    return billed, paid, waived


class SchoolSettings(TenantModel):
    """The switches that change how this institution uses EduSphere.

    One row per institution, holding the school mode.
    """
    class Mode(models.TextChoices):
        SCHOOL = "school", "School mode: classes, grades and subjects"
        COLLEGE = "college", "College mode: programmes, years and credits"
    mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.COLLEGE)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["institution"], name="uniq_school_settings_institution")
        ]

    @classmethod
    def get(cls, institution=None):
        """The settings row of one institution, created on first use.

        Called with no argument it uses the current institution. Called with
        an institution (or its pk) it reads that institution's row even when
        another institution is current, so the row is looked up through
        `all_objects` instead of the tenant-scoped manager.
        """
        from core.tenancy import current_institution_id

        if institution is None:
            institution_id = current_institution_id()
        else:
            institution_id = institution.pk if hasattr(institution, "pk") else institution
        if institution_id is None:
            return None
        obj, _created = cls.all_objects.get_or_create(institution_id=institution_id)
        return obj

    @property
    def is_school(self):
        return self.mode == self.Mode.SCHOOL

    def __str__(self):
        return f"School settings ({self.get_mode_display()})"


class AcademicYear(TenantModel):
    name = models.CharField(max_length=20, help_text="For example 2026")
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(default=False)
    is_closed = models.BooleanField(default=False,
        help_text="A closed year cannot be changed any more.")
    closed_on = models.DateField(null=True, blank=True)
    class Meta:
        ordering = ["-name"]
        constraints = [
            models.UniqueConstraint(fields=["institution", "name"], name="uniq_year_name"),
            models.UniqueConstraint(fields=["institution"], condition=Q(is_current=True),
                                    name="uniq_current_year"),
        ]

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.end_date <= self.start_date:
            raise ValidationError({"end_date": "The end date must be after the start date."})

    def save(self, *a, **kw):
        # The de-duplication happens before the write, because the database
        # refuses two current years in one institution.
        if self.is_current:
            AcademicYear.all_objects.filter(is_current=True, institution=self.institution) \
                .exclude(pk=self.pk).update(is_current=False)
        super().save(*a, **kw)

    def __str__(self): return self.name


class Term(TenantModel):
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name="terms")
    name = models.CharField(max_length=40, help_text="For example Term 1 or Semester 2")
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(default=False)
    is_closed = models.BooleanField(default=False,
        help_text="A closed term cannot be changed any more.")
    closed_on = models.DateField(null=True, blank=True)
    class Meta:
        ordering = ["academic_year__name", "start_date"]
        unique_together = ("academic_year", "name")
        constraints = [
            models.UniqueConstraint(fields=["institution"], condition=Q(is_current=True),
                                    name="uniq_current_term"),
        ]

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.end_date <= self.start_date:
            raise ValidationError({"end_date": "The end date must be after the start date."})
        if self.academic_year_id and self.start_date and self.end_date:
            year = self.academic_year
            if self.start_date < year.start_date or self.end_date > year.end_date:
                raise ValidationError({"start_date": "Term dates must fall inside the academic year."})

    def save(self, *a, **kw):
        # Only one term may be current in an institution, so the others are
        # switched off before this one is written.
        if self.is_current:
            Term.all_objects.filter(is_current=True, institution=self.institution) \
                .exclude(pk=self.pk).update(is_current=False)
        super().save(*a, **kw)

    def __str__(self): return f"{self.name} ({self.academic_year.name})"


class GradeLevel(TenantModel):
    """One step on the way through the school: Grade 3, Form 2, Year 1.

    Classes hang off a grade level, so the promotion tool knows which class
    comes next. The order decides the way up: the level with the next higher
    number in the same kind is the next one.
    """

    class Kind(models.TextChoices):
        PRIMARY = "primary", "Primary"
        SECONDARY = "secondary", "Secondary"
        COLLEGE = "college", "College year"

    name = models.CharField(max_length=60, help_text="For example Grade 3")
    code = models.CharField(max_length=20, blank=True)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.SECONDARY)
    order = models.PositiveIntegerField(default=0,
        help_text="The step on the way up. Higher numbers come later.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["kind", "order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["institution", "name"], name="uniq_grade_level_name")
        ]

    def __str__(self): return self.name

    @property
    def next_level(self):
        """The level a student moves to after this one, if there is one."""
        return GradeLevel.all_objects.filter(
            institution=self.institution, kind=self.kind, is_active=True, order__gt=self.order
        ).order_by("order", "name").first()


class SchoolClass(TenantModel):
    class Level(models.TextChoices):
        PRIMARY = "primary", "Primary"
        SECONDARY = "secondary", "Secondary"
    name = models.CharField(max_length=60, help_text="For example Grade 3 or Form 2")
    level = models.CharField(max_length=20, choices=Level.choices, default=Level.PRIMARY)
    grade_level = models.ForeignKey(GradeLevel, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="classes",
        help_text="The step this class belongs to. Used by the promotion tool.")
    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["institution", "name"], name="uniq_class_name")
        ]
    def __str__(self): return self.name


class Stream(TenantModel):
    school_class = models.ForeignKey(SchoolClass, on_delete=models.CASCADE, related_name="streams")
    name = models.CharField(max_length=30, help_text="For example A or Blue")
    class Meta:
        ordering = ["school_class__name", "name"]
        unique_together = ("school_class", "name")
    def __str__(self): return f"{self.school_class.name} {self.name}"


class ClassTeacher(TenantModel):
    school_class = models.ForeignKey(SchoolClass, on_delete=models.CASCADE, related_name="class_teachers")
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name="class_teachers")
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, limit_choices_to={"role__in": ("lecturer", "admin")},
        related_name="classes_taught")
    class Meta:
        unique_together = ("school_class", "academic_year")
    def __str__(self): return f"{self.school_class} – {self.teacher}"


class Subject(TenantModel):
    class Level(models.TextChoices):
        PRIMARY = "primary", "Primary"
        SECONDARY = "secondary", "Secondary"
        COLLEGE = "college", "College / institute"
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=150)
    level = models.CharField(max_length=20, choices=Level.choices, default=Level.SECONDARY)
    lecturer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, limit_choices_to={"role": "lecturer"},
        related_name="subjects_taught", help_text="Teacher who enters this subject's marks.")
    class Meta:
        ordering = ["level", "code"]
        verbose_name_plural = "subjects"
        constraints = [
            models.UniqueConstraint(fields=["institution", "code"], name="uniq_subject_code")
        ]
    def __str__(self): return f"{self.code} – {self.name}"


class ClassSubjectAllocation(TenantModel):
    """The subjects a class takes in a year, and who teaches them.

    This is the list the marks sheet, the timetable and the report card work
    from: a class has a subject in a year because it was allocated.
    """

    school_class = models.ForeignKey(SchoolClass, on_delete=models.CASCADE, related_name="allocations")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="allocations")
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name="allocations")
    lecturer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, limit_choices_to={"role__in": ("lecturer", "admin")},
        related_name="allocations")
    lessons_per_week = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["school_class__name", "subject__code"]
        unique_together = ("school_class", "subject", "academic_year")
        verbose_name = "class subject allocation"
        verbose_name_plural = "class subject allocations"

    def __str__(self):
        return f"{self.school_class} – {self.subject} ({self.academic_year.name})"


class TimetableEntry(TenantModel):
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name="lessons")
    term = models.ForeignKey(Term, on_delete=models.CASCADE, related_name="lessons")
    day = models.PositiveSmallIntegerField(choices=DAY_CHOICES)
    start_time = models.TimeField()
    end_time = models.TimeField()
    subject = models.ForeignKey(Subject, null=True, blank=True, on_delete=models.CASCADE,
        related_name="lessons", help_text="School mode: pick a subject and a class.")
    course = models.ForeignKey("Course", null=True, blank=True, on_delete=models.CASCADE,
        related_name="lessons", help_text="College mode: pick a course.")
    school_class = models.ForeignKey(SchoolClass, null=True, blank=True,
        on_delete=models.CASCADE, related_name="lessons")
    stream = models.ForeignKey(Stream, null=True, blank=True, on_delete=models.CASCADE,
        related_name="lessons", help_text="Leave empty to teach the whole class.")
    lecturer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        limit_choices_to={"role__in": ("lecturer", "admin")}, related_name="lessons")
    venue = models.CharField(max_length=60, blank=True)
    class Meta:
        ordering = ["day", "start_time"]

    @property
    def title(self):
        if self.subject_id:
            return str(self.subject)
        if self.course_id:
            return str(self.course)
        return "Lesson"

    @property
    def group(self):
        if self.school_class_id:
            return str(self.stream) if self.stream_id else str(self.school_class)
        return str(self.course.programme) if self.course_id else "—"

    def _clash_errors(self):
        if not (self.term_id and self.day and self.start_time and self.end_time):
            return []
        qs = TimetableEntry.objects.filter(term_id=self.term_id, day=self.day,
            start_time__lt=self.end_time, end_time__gt=self.start_time)
        if self.pk:
            qs = qs.exclude(pk=self.pk)
        errors = []
        day = self.get_day_display()
        if self.lecturer_id:
            other = qs.filter(lecturer_id=self.lecturer_id).select_related("subject", "course").first()
            if other:
                name = self.lecturer.get_full_name() or self.lecturer.username
                errors.append(f"{name} is already teaching {other.title} from "
                              f"{other.start_time:%H:%M} to {other.end_time:%H:%M} on {day}.")
        if self.school_class_id:
            if self.stream_id:
                class_qs = qs.filter(school_class_id=self.school_class_id).filter(
                    Q(stream__isnull=True) | Q(stream_id=self.stream_id))
            else:
                class_qs = qs.filter(school_class_id=self.school_class_id)
            other = class_qs.select_related("subject", "course", "stream").first()
            if other:
                errors.append(f"{self.school_class} already has {other.title} from "
                              f"{other.start_time:%H:%M} to {other.end_time:%H:%M} on {day}.")
        if self.course_id:
            other = qs.filter(course_id=self.course_id).select_related("course").first()
            if other:
                errors.append(f"{self.course} is already scheduled from {other.start_time:%H:%M} "
                              f"to {other.end_time:%H:%M} on {day}.")
        if self.venue:
            other = qs.filter(venue__iexact=self.venue).first()
            if other:
                errors.append(f"Venue {self.venue} is already booked from {other.start_time:%H:%M} "
                              f"to {other.end_time:%H:%M} on {day}.")
        return errors

    def clean(self):
        super().clean()
        if bool(self.subject_id) == bool(self.course_id):
            raise ValidationError("Choose either a subject or a course for this lesson, "
                                  "not both and not neither.")
        if self.subject_id and not self.school_class_id:
            raise ValidationError({"school_class": "Choose the class this subject is taught to."})
        if self.course_id and self.school_class_id:
            raise ValidationError({"school_class": "College lessons are not tied to a class."})
        if self.stream_id and (not self.school_class_id
                               or self.stream.school_class_id != self.school_class_id):
            raise ValidationError({"stream": "That stream belongs to another class."})
        if self.term_id and self.academic_year_id and self.term.academic_year_id != self.academic_year_id:
            raise ValidationError({"term": "That term belongs to a different academic year."})
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError({"end_time": "The lesson must end after it starts."})
        errors = self._clash_errors()
        if errors:
            raise ValidationError(errors)

    def save(self, *a, **kw):
        self.full_clean()
        super().save(*a, **kw)

    @classmethod
    def for_student(cls, student):
        if student.school_class_id:
            qs = cls.objects.filter(school_class_id=student.school_class_id).filter(
                Q(stream__isnull=True) | Q(stream_id=student.stream_id))
        elif student.programme_id:
            qs = cls.objects.filter(course__enrollments__student=student)
        else:
            return cls.objects.none()
        if Term.objects.filter(is_current=True).exists():
            qs = qs.filter(term__is_current=True)
        return qs.select_related("subject", "course", "lecturer", "school_class", "stream",
                                 "course__programme").distinct()

    def __str__(self): return f"{self.get_day_display()} {self.start_time:%H:%M} {self.title}"


class Programme(TenantModel):
    class Level(models.TextChoices):
        PRIMARY = "primary", "Primary school"
        SECONDARY = "secondary", "Secondary school"
        SHORT = "short", "Short course"
        CERT = "certificate", "Certificate"
        DIPLOMA = "diploma", "Diploma"
        DEGREE = "degree", "Bachelor's degree"
        MASTERS = "masters", "Master's degree"
        PHD = "phd", "Doctorate"
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=20)
    level = models.CharField(max_length=20, choices=Level.choices)
    duration_months = models.PositiveIntegerField(default=12)
    tuition_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["institution", "code"], name="uniq_programme_code")
        ]
    def __str__(self): return f"{self.name} ({self.get_level_display()})"


class Course(TenantModel):
    code = models.CharField(max_length=20)
    title = models.CharField(max_length=150)
    programme = models.ForeignKey(Programme, on_delete=models.CASCADE, related_name="courses")
    credits = models.PositiveSmallIntegerField(default=3)
    lecturer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, limit_choices_to={"role": "lecturer"}, related_name="courses")
    class Meta:
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(fields=["institution", "code"], name="uniq_course_code")
        ]
    def __str__(self): return f"{self.code} – {self.title}"


class ProgrammeYear(TenantModel):
    """One year of a programme: Diploma in IT, Year 1, 2026.

    A programme year carries the rules that only apply to that year: how many
    students it takes, how many credits a student may carry and when the
    students may register.
    """

    programme = models.ForeignKey(Programme, on_delete=models.CASCADE, related_name="years")
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE,
        related_name="programme_years")
    name = models.CharField(max_length=60, blank=True)
    number = models.PositiveSmallIntegerField(default=1)
    capacity = models.PositiveIntegerField(default=0, help_text="0 means no limit.")
    credit_limit = models.PositiveSmallIntegerField(default=0, help_text="0 means no limit.")
    registration_opens = models.DateField(null=True, blank=True)
    registration_closes = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-academic_year__name", "number"]
        constraints = [
            models.UniqueConstraint(fields=["programme", "academic_year"], name="uniq_programme_year")
        ]

    @property
    def label(self):
        return self.name or f"Year {self.number}"

    def __str__(self):
        return f"{self.programme.code} {self.label}"


class Cohort(TenantModel):
    """A group of students that goes through the programme year together."""

    programme_year = models.ForeignKey(ProgrammeYear, on_delete=models.CASCADE, related_name="cohorts")
    name = models.CharField(max_length=60)
    lecturer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, limit_choices_to={"role": "lecturer"},
        related_name="cohorts")

    class Meta:
        ordering = ["programme_year__programme__code", "name"]
        constraints = [
            models.UniqueConstraint(fields=["programme_year", "name"], name="uniq_cohort_name")
        ]

    def __str__(self):
        return self.name


class CourseOffering(TenantModel):
    """A course as it is taught in one term: IT101 in Term 1 of 2026."""

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="offerings")
    term = models.ForeignKey(Term, on_delete=models.CASCADE, related_name="offerings")
    lecturer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, limit_choices_to={"role": "lecturer"},
        related_name="offerings")
    capacity = models.PositiveIntegerField(default=0, help_text="0 means no limit.")
    credits = models.PositiveSmallIntegerField(null=True, blank=True,
        help_text="Leave empty to use the credits of the course.")
    enrolment_deadline = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["term__start_date", "course__code"]
        constraints = [
            models.UniqueConstraint(fields=["course", "term"], name="uniq_offering_course_term")
        ]

    @property
    def credit_points(self):
        return self.course.credits if self.credits is None else self.credits

    @property
    def registered_count(self):
        return Enrollment.objects.filter(
            offering=self, status=Enrollment.Status.REGISTERED).count()

    @property
    def places_left(self):
        if not self.capacity:
            return None
        return max(self.capacity - self.registered_count, 0)

    def __str__(self):
        return f"{self.course.code} – {self.term}"


class CoursePrerequisite(TenantModel):
    """A course a student must pass before taking another course."""

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="prerequisites")
    required = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="required_by")
    min_mark = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Leave empty to accept any pass.")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["course", "required"], name="uniq_prerequisite")
        ]

    def __str__(self):
        return f"{self.course.code} needs {self.required.code}"


class Student(TenantModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ENROLLED = "enrolled", "Enrolled"
        DEFERRED = "deferred", "Deferred"
        SUSPENDED = "suspended", "Suspended"
        TRANSFERRED_OUT = "transferred_out", "Transferred out"
        WITHDRAWN = "withdrawn", "Withdrawn"
        GRADUATED = "graduated", "Graduated"
        ALUMNI = "alumni", "Alumni"
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="student")
    student_no = models.CharField(max_length=20, blank=True)
    programme = models.ForeignKey(Programme, on_delete=models.PROTECT, related_name="students")
    programme_year = models.ForeignKey(ProgrammeYear, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="students",
        help_text="The year of the programme the student is in.")
    cohort = models.ForeignKey(Cohort, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="students")
    school_class = models.ForeignKey(SchoolClass, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="students")
    stream = models.ForeignKey(Stream, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="students")
    year_or_grade = models.CharField("Year / Grade", max_length=20, default="1")
    date_of_birth = models.DateField(null=True, blank=True)
    guardian_name = models.CharField(max_length=120, blank=True)
    guardian_phone = models.CharField(max_length=30, blank=True)
    photo = models.ImageField(upload_to="students/photos/", blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["institution", "student_no"], name="uniq_student_no")
        ]

    def save(self, *a, **kw):
        adding = self._state.adding
        with transaction.atomic():
            if adding and not self.student_no:
                from core.numbering import next_number

                self.student_no = next_number(
                    self.institution, "student",
                    programme_code=getattr(self.programme, "code", ""))
            elif self.student_no:
                # An administrator may type a number in any case. Store it in
                # one consistent shape so matching and searching are reliable.
                self.student_no = str(self.student_no).strip().upper()
            super().save(*a, **kw)

    @property
    def name(self): return self.user.get_full_name() or self.user.username

    @property
    def class_label(self):
        if not self.school_class_id:
            return self.year_or_grade
        if self.stream_id:
            return f"{self.school_class.name} {self.stream.name}"
        return str(self.school_class)

    @property
    def total_billed(self):
        return self.invoices.aggregate(t=Sum("amount"))["t"] or Decimal("0")
    @property
    def total_paid(self):
        from finance.models import Payment
        return Payment.objects.filter(invoice__student=self).aggregate(t=Sum("amount"))["t"] or Decimal("0")
    @property
    def total_waived(self):
        from finance.models import Waiver
        return Waiver.objects.filter(invoice__student=self).aggregate(t=Sum("amount"))["t"] or Decimal("0")
    @property
    def balance(self):
        from finance.services import student_balance
        return student_balance(self)
    def __str__(self): return f"{self.student_no} {self.name}"


class Guardian(TenantModel):
    """Links a parent account to a student so the parent can follow their progress."""
    parent = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                               related_name="wards")
    student = models.ForeignKey(Student, on_delete=models.CASCADE,
                                related_name="guardian_links")
    relationship = models.CharField(max_length=40, blank=True)
    can_view_finance = models.BooleanField(default=True)
    linked = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("parent", "student")
        ordering = ["student__user__last_name"]

    def __str__(self):
        return f"{self.parent.get_full_name() or self.parent.username} – {self.student.name}"


GuardianLink = Guardian


class GradeBand(TenantModel):
    """One row per grade: a mark of min_mark or higher earns this letter and points."""
    letter = models.CharField(max_length=5)
    min_mark = models.DecimalField(max_digits=5, decimal_places=2,
                                   validators=[MinValueValidator(0)])
    points = models.DecimalField(max_digits=4, decimal_places=2, default=0)

    class Meta:
        ordering = ["-min_mark"]
        verbose_name_plural = "grade bands"
        constraints = [
            models.UniqueConstraint(fields=["institution", "letter"], name="uniq_grade_band_letter")
        ]

    def clean(self):
        super().clean()
        self.letter = self.letter.strip()
        if not self.letter:
            raise ValidationError({"letter": "Give the grade a letter, for example A."})
        if not 0 <= self.min_mark <= 100:
            raise ValidationError({"min_mark": "The minimum mark must be between 0 and 100."})
        clash = GradeBand.objects.filter(letter__iexact=self.letter)
        if self.pk:
            clash = clash.exclude(pk=self.pk)
        if clash.exists():
            raise ValidationError({"letter": f"Grade {self.letter} already exists."})

    def save(self, *a, **kw):
        self.full_clean()
        super().save(*a, **kw)

    @classmethod
    def grade_for(cls, mark, bands=None):
        """Return (letter, points) for a mark. Falls back to the default A–F scale.

        Pass `bands` when grading several marks in a row so the scale is read
        once instead of once per mark.
        """
        if mark is None:
            return "—", None
        if bands is None:
            bands = list(cls.objects.order_by("-min_mark"))
        if not bands:
            bands = [cls(letter=letter, min_mark=Decimal(mark_min), points=Decimal(pts))
                     for letter, mark_min, pts in DEFAULT_GRADE_BANDS]
        for b in bands:
            if mark >= b.min_mark:
                return b.letter, b.points
        return bands[-1].letter, bands[-1].points

    def __str__(self): return f"{self.letter} from {self.min_mark:g}"


class AwardRule(TenantModel):
    """One final award band: a cumulative GPA at or above min_gpa earns this label.

    Rules can apply to one programme level or to every programme. A saved rule
    set for the student's level replaces the all-programme set, which in turn
    replaces the built-in defaults.
    """

    class Level(models.TextChoices):
        ANY = "", "All programmes"
        PRIMARY = "primary", "Primary school"
        SECONDARY = "secondary", "Secondary school"
        SHORT = "short", "Short course"
        CERT = "certificate", "Certificate"
        DIPLOMA = "diploma", "Diploma"
        DEGREE = "degree", "Bachelor's degree"
        MASTERS = "masters", "Master's degree"
        PHD = "phd", "Doctorate"

    level = models.CharField(max_length=20, choices=Level.choices,
                             blank=True, default="")
    label = models.CharField(max_length=60)
    min_gpa = models.DecimalField(max_digits=5, decimal_places=2,
                                  validators=[MinValueValidator(0)])

    class Meta:
        ordering = ["-min_gpa", "label"]
        constraints = [
            models.UniqueConstraint(fields=["institution", "level", "label"],
                                    name="uniq_award_rule_level_label"),
        ]

    def clean(self):
        super().clean()
        self.label = self.label.strip()
        if not self.label:
            raise ValidationError({"label": "Give the award a name."})
        if self.min_gpa is not None and self.min_gpa < 0:
            raise ValidationError({"min_gpa": "The minimum GPA cannot be negative."})
        clash = AwardRule.objects.filter(level=self.level, label__iexact=self.label)
        if self.pk:
            clash = clash.exclude(pk=self.pk)
        if clash.exists():
            raise ValidationError(
                {"label": f"{self.label} already exists for this level."})

    def save(self, *a, **kw):
        self.full_clean()
        super().save(*a, **kw)

    @classmethod
    def for_level(cls, level):
        """The rules to use for a programme level, preferring the most specific."""
        specific = list(cls.objects.filter(level=level).order_by("-min_gpa", "label"))
        if specific:
            return specific
        generic = list(cls.objects.filter(level="").order_by("-min_gpa", "label"))
        if generic:
            return generic
        defaults = DEFAULT_AWARD_RULES.get(level, DEFAULT_AWARD_RULES[""])
        return [cls(level=level, label=label, min_gpa=Decimal(min_gpa))
                for label, min_gpa in defaults]

    @classmethod
    def award_for(cls, gpa, level, rules=None):
        """The award label for a GPA at this programme level, or None."""
        if gpa is None:
            return None
        if rules is None:
            rules = cls.for_level(level)
        for rule in sorted(rules, key=lambda r: r.min_gpa, reverse=True):
            if gpa >= rule.min_gpa:
                return rule.label
        return None

    def __str__(self):
        level = self.get_level_display() if self.level else "All programmes"
        return f"{self.label} from {self.min_gpa:g} ({level})"


class AssessmentComponent(TenantModel):
    """One weighted part of a course or subject, such as CA or the final exam.

    The weights are relative rather than required to add up to 100, so a
    teacher can set up the components before deciding the final split. The
    weighted mark is only awarded once every component has a mark.
    """

    course = models.ForeignKey(Course, null=True, blank=True, on_delete=models.CASCADE,
        related_name="assessment_components")
    subject = models.ForeignKey(Subject, null=True, blank=True, on_delete=models.CASCADE,
        related_name="assessment_components")
    name = models.CharField(max_length=60, help_text="For example CA or Final exam")
    weight = models.DecimalField(max_digits=5, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01")), MaxValueValidator(100)])
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["course", "name"],
                condition=Q(course__isnull=False), name="uniq_course_component_name"),
            models.UniqueConstraint(fields=["subject", "name"],
                condition=Q(subject__isnull=False), name="uniq_subject_component_name"),
        ]

    def clean(self):
        super().clean()
        if bool(self.course_id) == bool(self.subject_id):
            raise ValidationError("Choose either a course or a subject for this component.")
        self.name = self.name.strip()
        if not self.name:
            raise ValidationError({"name": "Give the component a name, for example CA."})
        field = "course" if self.course_id else "subject"
        clash = AssessmentComponent.objects.filter(
            **{field: getattr(self, f"{field}_id")}, name__iexact=self.name)
        if self.pk:
            clash = clash.exclude(pk=self.pk)
        if clash.exists():
            raise ValidationError({"name": f"{self.name} already exists here."})

    def save(self, *a, **kw):
        self.full_clean()
        super().save(*a, **kw)

    def belongs_to(self, enrollment):
        if self.course_id:
            return enrollment.course_id == self.course_id
        return enrollment.subject_id == self.subject_id

    def __str__(self):
        return f"{self.name} ({self.weight:g}%)"


class Enrollment(TenantModel):
    class Status(models.TextChoices):
        REGISTERED = "registered", "Registered"
        WAITLISTED = "waitlisted", "On the waiting list"
        DROPPED = "dropped", "Dropped"

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="enrollments")
    course = models.ForeignKey(Course, null=True, blank=True, on_delete=models.CASCADE,
        related_name="enrollments")
    subject = models.ForeignKey(Subject, null=True, blank=True, on_delete=models.CASCADE,
        related_name="enrollments")
    term = models.ForeignKey(Term, null=True, blank=True, on_delete=models.PROTECT,
        related_name="enrollments")
    offering = models.ForeignKey(CourseOffering, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="enrollments", help_text="Set when the course is taken as an offering.")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REGISTERED)
    mark = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["student", "course", "term"],
                condition=Q(course__isnull=False), name="uniq_enrol_course"),
            models.UniqueConstraint(fields=["student", "subject", "term"],
                condition=Q(subject__isnull=False), name="uniq_enrol_subject"),
        ]

    def clean(self):
        super().clean()
        if bool(self.course_id) == bool(self.subject_id):
            raise ValidationError("Choose either a course or a subject, not both and not neither.")
        if self.offering_id:
            if self.course_id and self.offering.course_id != self.course_id:
                raise ValidationError({"offering": "The offering is for another course."})
            if self.term_id and self.offering.term_id != self.term_id:
                raise ValidationError({"offering": "The offering is taught in another term."})


    def save(self, *a, **kw):
        self.full_clean()
        super().save(*a, **kw)

    @property
    def label(self): return self.course or self.subject

    @property
    def grade(self):
        return GradeBand.grade_for(self.mark)[0]

    @property
    def points(self):
        return GradeBand.grade_for(self.mark)[1]

    @classmethod
    def cohort_table(cls, enrollments):
        """Return {enrollment_pk: (position, cohort_size)} for the given enrollments,
        comparing each row against everyone else with a mark on the same
        course or subject and term. Uses one extra query."""
        keys = {(e.course_id, e.subject_id, e.term_id) for e in enrollments}
        if not keys:
            return {}
        query = Q()
        for course_id, subject_id, term_id in keys:
            query |= Q(course_id=course_id, subject_id=subject_id, term_id=term_id)
        rows = list(cls.objects.filter(mark__isnull=False).filter(query)
                    .select_related("student__user")
                    .order_by("-mark", "student__user__last_name",
                              "student__user__first_name", "pk"))
        totals = Counter((r.course_id, r.subject_id, r.term_id) for r in rows)
        seen, out = Counter(), {}
        for r in rows:
            key = (r.course_id, r.subject_id, r.term_id)
            seen[key] += 1
            out[r.pk] = (seen[key], totals[key])
        return out

    def __str__(self): return f"{self.student} – {self.label}"


class AssessmentMark(TenantModel):
    """A student's score for one assessment component."""

    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE,
        related_name="component_marks")
    component = models.ForeignKey(AssessmentComponent, on_delete=models.CASCADE,
        related_name="marks")
    mark = models.DecimalField(max_digits=5, decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)])

    class Meta:
        ordering = ["component__order", "component__name"]
        constraints = [
            models.UniqueConstraint(fields=["enrollment", "component"],
                name="uniq_enrollment_component_mark"),
        ]

    def clean(self):
        super().clean()
        if self.component_id and self.enrollment_id \
                and not self.component.belongs_to(self.enrollment):
            raise ValidationError({"component": "That component belongs to another course or subject."})
        if self.mark is not None and not 0 <= self.mark <= 100:
            raise ValidationError({"mark": "The mark must be between 0 and 100."})

    def save(self, *a, **kw):
        self.full_clean()
        super().save(*a, **kw)

    def __str__(self):
        return f"{self.enrollment} {self.component.name}: {self.mark}"


class ResultSheet(TenantModel):
    """The moderation and release state of one mark sheet for one term.

    A sheet belongs to a course or a subject, never both. Marks are entered on
    the roster; this row records who submitted them, who moderated and
    approved them, and when they were released to students.
    """

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted for moderation"
        MODERATED = "moderated", "Moderated"
        APPROVED = "approved", "Approved"
        RELEASED = "released", "Released to students"

    course = models.ForeignKey(Course, null=True, blank=True, on_delete=models.CASCADE,
        related_name="result_sheets")
    subject = models.ForeignKey(Subject, null=True, blank=True, on_delete=models.CASCADE,
        related_name="result_sheets")
    term = models.ForeignKey(Term, on_delete=models.CASCADE, related_name="result_sheets")
    status = models.CharField(max_length=20, choices=Status.choices,
                              default=Status.SUBMITTED)
    entered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="result_sheets_entered")
    submitted_at = models.DateTimeField(null=True, blank=True)
    moderated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="result_sheets_moderated")
    moderated_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="result_sheets_approved")
    approved_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="result_sheets_released")
    released_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-term__start_date", "course__code", "subject__code"]
        constraints = [
            models.UniqueConstraint(fields=["course", "term"],
                condition=Q(course__isnull=False), name="uniq_course_result_sheet"),
            models.UniqueConstraint(fields=["subject", "term"],
                condition=Q(subject__isnull=False), name="uniq_subject_result_sheet"),
        ]

    def clean(self):
        super().clean()
        if bool(self.course_id) == bool(self.subject_id):
            raise ValidationError("A result sheet belongs to either a course or a subject.")

    def save(self, *a, **kw):
        self.full_clean()
        super().save(*a, **kw)

    def belongs_to(self, enrollment):
        if self.course_id:
            return enrollment.course_id == self.course_id
        return enrollment.subject_id == self.subject_id

    def __str__(self):
        return f"{self.course or self.subject} - {self.term}"


class ResultClearance(TenantModel):
    """A bursar's written decision to release withheld results despite fees."""

    student = models.ForeignKey(Student, on_delete=models.CASCADE,
                                related_name="result_clearances")
    term = models.ForeignKey(Term, on_delete=models.CASCADE,
                             related_name="result_clearances")
    reason = models.TextField()
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="result_clearances")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]
        constraints = [
            models.UniqueConstraint(fields=["student", "term"],
                                    name="uniq_result_clearance_student_term"),
        ]

    def clean(self):
        super().clean()
        self.reason = self.reason.strip()
        if not self.reason:
            raise ValidationError({"reason": "Give a reason for releasing the results."})

    def save(self, *a, **kw):
        self.full_clean()
        super().save(*a, **kw)

    def __str__(self):
        return f"{self.student} - {self.term}: {self.reason}"


class DocumentVerification(TenantModel):
    """A stable, unguessable token printed on a report card or transcript."""

    class Kind(models.TextChoices):
        REPORT_CARD = "report_card", "Report card"
        TRANSCRIPT = "transcript", "Academic transcript"

    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    student = models.ForeignKey(Student, on_delete=models.CASCADE,
                                related_name="document_verifications")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="document_verifications_issued")
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-issued_at"]
        constraints = [
            models.UniqueConstraint(fields=["student", "kind"],
                                    name="uniq_document_verification_student_kind"),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} for {self.student}"


class AtRiskScore(TenantModel):
    """One night's risk result for a student, kept so trends can be reviewed."""

    student = models.ForeignKey(Student, on_delete=models.CASCADE,
                                related_name="risk_scores")
    computed_on = models.DateField()
    score = models.PositiveSmallIntegerField(default=0)
    reasons = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-computed_on", "-score"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "student", "computed_on"],
                name="uniq_at_risk_student_day"),
        ]

    def __str__(self):
        return f"{self.student} {self.score} on {self.computed_on}"


class Activity(TenantModel):
    class Kind(models.TextChoices):
        ASSIGN = "assignment", "Assignment"
        TEST = "test", "Test / Exam"
        CLUB = "club", "Club / Society"
        SPORT = "sport", "Sport"
        PROJECT = "project", "Project"
        DISCIPLINE = "discipline", "Discipline note"
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="activities")
    course = models.ForeignKey("Course", null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="activities")
    subject = models.ForeignKey("Subject", null=True, blank=True,
                                on_delete=models.SET_NULL, related_name="activities")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    title = models.CharField(max_length=150)
    date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    detail = models.TextField(blank=True)
    attachment = models.FileField(upload_to="activities/", blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL)
    class Meta:
        ordering = ["-date"]

    @property
    def is_assignment(self):
        return self.kind == self.Kind.ASSIGN


class Assignment(Activity):
    class Meta:
        proxy = True


class AssignmentSubmission(TenantModel):
    assignment = models.ForeignKey(Activity, on_delete=models.CASCADE,
                                   related_name="submissions")
    student = models.ForeignKey(Student, on_delete=models.CASCADE,
                                related_name="assignment_submissions")
    text = models.TextField(blank=True)
    file = models.FileField(upload_to="assignments/submissions/", blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)
    feedback = models.TextField(blank=True)
    mark = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    graded_at = models.DateTimeField(null=True, blank=True)
    graded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL)

    class Meta:
        unique_together = ("assignment", "student")
        ordering = ["-submitted_at"]


class Attendance(TenantModel):
    class Status(models.TextChoices):
        PRESENT = "present", "Present"
        LATE = "late", "Late"
        ABSENT = "absent", "Absent"
        EXCUSED = "excused", "Excused absence"

    ATTENDED = (Status.PRESENT, Status.LATE)
    MISSED = (Status.ABSENT, Status.EXCUSED)
    LETTERS = {Status.PRESENT: "P", Status.LATE: "L",
               Status.ABSENT: "A", Status.EXCUSED: "E"}

    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE, related_name="attendance")
    date = models.DateField()
    present = models.BooleanField(default=True,
        help_text="Kept in step with the status: present and late both count as attended.")
    status = models.CharField(max_length=20, choices=Status.choices,
                              default=Status.PRESENT)
    reason = models.CharField(max_length=200, blank=True,
        help_text="Why the student was absent, if known.")
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL)
    guardians_notified = models.BooleanField(default=False, editable=False,
        help_text="The parents have already been told about this absence.")

    class Meta:
        unique_together = ("enrollment", "date")

    def save(self, *a, **kw):
        if self.status in self.ATTENDED:
            self.present = True
        elif self.status in self.MISSED:
            self.present = False
        # ``update_or_create`` saves only the keys it was given, so a status
        # change would otherwise leave the stored ``present`` flag behind.
        update_fields = kw.get("update_fields")
        if update_fields is not None and "status" in update_fields:
            kw["update_fields"] = set(update_fields) | {"present"}
        super().save(*a, **kw)

    @property
    def letter(self):
        return self.LETTERS.get(self.status, "–")

    @property
    def attended(self):
        return self.status in self.ATTENDED

    @classmethod
    def rates_by_term(cls, enrollments):
        """Return {term_label: (sessions, present)} for the given enrollments."""
        term_of = {e.pk: (e.term.name if e.term else "No term") for e in enrollments}
        out = {}
        for eid, present in cls.objects.filter(enrollment_id__in=term_of.keys()) \
                                        .values_list("enrollment_id", "present"):
            sessions, marked = out.setdefault(term_of[eid], [0, 0])
            out[term_of[eid]] = [sessions + 1, marked + (1 if present else 0)]
        return {t: (s, m) for t, (s, m) in out.items()}

    @classmethod
    def register(cls, enrollments):
        """Return (dates, {enrollment_pk: {date: status}}) for a class register."""
        rows = cls.objects.filter(enrollment_id__in=[e.pk for e in enrollments]) \
                          .values_list("enrollment_id", "date", "status")
        dates, grid = [], {}
        for eid, d, status in rows:
            if d not in dates:
                dates.append(d)
            grid.setdefault(eid, {})[d] = status
        return sorted(dates), grid

    @classmethod
    def absences(cls, enrollments, limit=10):
        """The most recent absent or excused rows, newest first."""
        return list(cls.objects.filter(enrollment_id__in=[e.pk for e in enrollments],
                                       status__in=cls.MISSED)
                       .select_related("enrollment__course", "enrollment__subject")
                       .order_by("-date", "pk")[:limit])


class Announcement(TenantModel):
    title = models.CharField(max_length=150)
    body = models.TextField()
    audience = models.CharField(max_length=20, default="all",
        choices=[("all", "Everyone"), ("student", "Students"),
                 ("lecturer", "Lecturers"), ("parent", "Parents"),
                 ("staff", "Staff")])
    school_class = models.ForeignKey(SchoolClass, null=True, blank=True,
        on_delete=models.CASCADE, related_name="announcements",
        help_text="Optional: only this class sees the notice.")
    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="announcements")
    posted = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-posted"]

    def __str__(self):
        return self.title

    @classmethod
    def visible_to(cls, user):
        """The notices this user should see, honouring audience and class targeting."""
        if not user.is_authenticated:
            return cls.objects.none()
        if user.role in ("admin", "registrar", "bursar"):
            return cls.objects.all()
        if user.role == "lecturer":
            q = Q(audience__in=("all", "lecturer", "staff"))
            class_ids = []
            try:
                class_ids = list(user.subjects_taught.values_list("school_class_id", flat=True))
                class_ids += list(user.courses.values_list("enrollments__student__school_class_id", flat=True))
            except Exception:
                pass
            if class_ids:
                q &= Q(school_class__isnull=True) | Q(school_class_id__in=set(c for c in class_ids if c))
            return cls.objects.filter(q).distinct()
        if user.role == "student":
            q = Q(audience__in=("all", "student"))
            class_id = getattr(user, "student", None) and user.student.school_class_id
            if class_id:
                q &= Q(school_class__isnull=True) | Q(school_class_id=class_id)
            else:
                q &= Q(school_class__isnull=True)
            return cls.objects.filter(q)
        if user.role == "parent":
            class_ids = list(user.wards.values_list("student__school_class_id",
                                                    flat=True)
                             .exclude(student__school_class_id=None))
            q = Q(audience__in=("all", "student", "parent"))
            q &= Q(school_class__isnull=True) | Q(school_class_id__in=class_ids)
            return cls.objects.filter(q)
        if user.role in ("dean", "hod", "accountant"):
            return cls.objects.filter(audience__in=("all", "staff"))
        return cls.objects.none()


class CalendarEvent(TenantModel):
    """One dated entry on the academic calendar: a holiday, exams, a meeting.

    Events without a class are shown to the whole institution on the
    dashboards and portals; an event with a class only reaches that class.
    """

    class Kind(models.TextChoices):
        HOLIDAY = "holiday", "Holiday"
        EXAM = "exam", "Exams"
        MEETING = "meeting", "Meeting"
        DEADLINE = "deadline", "Deadline"
        EVENT = "event", "Event"

    title = models.CharField(max_length=150)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.EVENT)
    starts_on = models.DateField()
    ends_on = models.DateField(null=True, blank=True,
        help_text="Leave empty for a one-day event.")
    detail = models.CharField(max_length=300, blank=True)
    school_class = models.ForeignKey(SchoolClass, null=True, blank=True,
        on_delete=models.CASCADE, related_name="calendar_events",
        help_text="Leave empty to show this event to the whole institution.")

    class Meta:
        ordering = ["starts_on", "pk"]

    def clean(self):
        super().clean()
        if self.ends_on and self.starts_on and self.ends_on < self.starts_on:
            raise ValidationError({"ends_on": "The event must end after it starts."})

    @property
    def when(self):
        """The dates as one readable label, for example 4–8 May 2026."""
        if self.ends_on and self.ends_on != self.starts_on:
            return f"{self.starts_on:%d %b %Y} – {self.ends_on:%d %b %Y}"
        return f"{self.starts_on:%d %b %Y}"

    @classmethod
    def upcoming(cls, user=None, limit=8, today=None):
        """Events from today onward that this user may see, soonest first.

        An event still running today (it started earlier and ends today or
        later) is included. Students only see institution-wide events and
        their own class's; parents see those of any linked child.
        """
        from django.utils import timezone

        today = today or timezone.localdate()
        qs = cls.objects.filter(
            Q(ends_on__gte=today) | Q(ends_on__isnull=True, starts_on__gte=today))
        if user is not None and user.is_authenticated:
            if user.role == "student":
                class_id = getattr(getattr(user, "student", None), "school_class_id", None)
                qs = qs.filter(Q(school_class__isnull=True)
                               | Q(school_class_id=class_id))
            elif user.role == "parent":
                class_ids = list(user.wards.values_list("student__school_class_id",
                                                        flat=True)
                                 .exclude(student__school_class_id=None))
                qs = qs.filter(Q(school_class__isnull=True)
                               | Q(school_class_id__in=class_ids))
        return list(qs.select_related("school_class")[:limit])

    def __str__(self):
        return f"{self.starts_on:%Y-%m-%d} {self.title}"


class ExamSitting(TenantModel):
    """A student's entry to sit a term's exams: candidate number, venue, clearance."""
    student = models.ForeignKey(Student, on_delete=models.CASCADE,
                                related_name="exam_sittings")
    term = models.ForeignKey(Term, on_delete=models.CASCADE,
                             related_name="exam_sittings")
    exam_no = models.CharField(max_length=24)
    venue = models.CharField(max_length=60, blank=True,
                             help_text="Exam room or hall.")
    cleared = models.BooleanField(default=True,
        help_text="Tick once the student may sit the exams.")
    note = models.CharField(max_length=120, blank=True,
        help_text="Reason shown to the student when they are not cleared.")

    class Meta:
        ordering = ["exam_no"]
        constraints = [
            models.UniqueConstraint(fields=["term", "student"],
                                    name="uniq_sitting_student"),
            models.UniqueConstraint(fields=["term", "exam_no"],
                                    name="uniq_sitting_number"),
        ]

    def __str__(self):
        return f"{self.exam_no} – {self.student}"

    @classmethod
    def enter(cls, term, students):
        """Give a candidate number to each student in `students` who is not
        entered yet. Returns how many sittings were created."""
        if hasattr(students, "order_by"):
            students = students.order_by("user__last_name", "user__first_name", "pk")
        have = set(cls.objects.filter(term=term).values_list("student_id", flat=True))
        used = set(cls.objects.filter(term=term).values_list("exam_no", flat=True))
        slug = "".join(ch for ch in term.name if ch.isalnum()).upper()[:8] or "EX"
        added = 0
        for st in students:
            if st.pk in have:
                continue
            seq = 0
            while True:
                seq += 1
                cand = f"{slug}-{seq:04d}"
                if cand not in used:
                    break
            cls.objects.create(student=st, term=term, exam_no=cand)
            used.add(cand)
            have.add(st.pk)
            added += 1
        return added
