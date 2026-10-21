from collections import Counter
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q, Sum

DAY_CHOICES = [(1, "Monday"), (2, "Tuesday"), (3, "Wednesday"), (4, "Thursday"),
               (5, "Friday"), (6, "Saturday"), (7, "Sunday")]

DEFAULT_GRADE_BANDS = [("A", "75", "4"), ("B", "65", "3"), ("C", "50", "2"),
                       ("D", "40", "1"), ("F", "0", "0")]


def balances_by_student():
    """Return ({student_id: billed}, {student_id: paid}, {student_id: waived})
    in three queries."""
    from finance.models import Invoice, Payment, Waiver
    billed = dict(Invoice.objects.values_list("student_id").annotate(t=Sum("amount")))
    paid = dict(Payment.objects.values_list("invoice__student_id").annotate(t=Sum("amount")))
    waived = dict(Waiver.objects.values_list("invoice__student_id").annotate(t=Sum("amount")))
    return billed, paid, waived


class SchoolSettings(models.Model):
    """One row (id=1) holding the switches that change how the school uses EduSphere."""
    class Mode(models.TextChoices):
        SCHOOL = "school", "School mode: classes, grades and subjects"
        COLLEGE = "college", "College mode: programmes, years and credits"
    mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.COLLEGE)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def is_school(self):
        return self.mode == self.Mode.SCHOOL

    def __str__(self):
        return f"School settings ({self.get_mode_display()})"


class AcademicYear(models.Model):
    name = models.CharField(max_length=20, unique=True, help_text="For example 2026")
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(default=False)
    class Meta: ordering = ["-name"]

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.end_date <= self.start_date:
            raise ValidationError({"end_date": "The end date must be after the start date."})

    def save(self, *a, **kw):
        super().save(*a, **kw)
        if self.is_current:
            AcademicYear.objects.filter(is_current=True).exclude(pk=self.pk).update(is_current=False)

    def __str__(self): return self.name


class Term(models.Model):
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name="terms")
    name = models.CharField(max_length=40, help_text="For example Term 1 or Semester 2")
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(default=False)
    class Meta:
        ordering = ["academic_year__name", "start_date"]
        unique_together = ("academic_year", "name")

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.end_date <= self.start_date:
            raise ValidationError({"end_date": "The end date must be after the start date."})
        if self.academic_year_id and self.start_date and self.end_date:
            year = self.academic_year
            if self.start_date < year.start_date or self.end_date > year.end_date:
                raise ValidationError({"start_date": "Term dates must fall inside the academic year."})

    def save(self, *a, **kw):
        super().save(*a, **kw)
        if self.is_current:
            Term.objects.filter(is_current=True).exclude(pk=self.pk).update(is_current=False)

    def __str__(self): return f"{self.name} ({self.academic_year.name})"


class SchoolClass(models.Model):
    class Level(models.TextChoices):
        PRIMARY = "primary", "Primary"
        SECONDARY = "secondary", "Secondary"
    name = models.CharField(max_length=60, unique=True, help_text="For example Grade 3 or Form 2")
    level = models.CharField(max_length=20, choices=Level.choices, default=Level.PRIMARY)
    class Meta: ordering = ["name"]
    def __str__(self): return self.name


class Stream(models.Model):
    school_class = models.ForeignKey(SchoolClass, on_delete=models.CASCADE, related_name="streams")
    name = models.CharField(max_length=30, help_text="For example A or Blue")
    class Meta:
        ordering = ["school_class__name", "name"]
        unique_together = ("school_class", "name")
    def __str__(self): return f"{self.school_class.name} {self.name}"


class ClassTeacher(models.Model):
    school_class = models.ForeignKey(SchoolClass, on_delete=models.CASCADE, related_name="class_teachers")
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name="class_teachers")
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, limit_choices_to={"role__in": ("lecturer", "admin")},
        related_name="classes_taught")
    class Meta:
        unique_together = ("school_class", "academic_year")
    def __str__(self): return f"{self.school_class} – {self.teacher}"


class Subject(models.Model):
    class Level(models.TextChoices):
        PRIMARY = "primary", "Primary"
        SECONDARY = "secondary", "Secondary"
        COLLEGE = "college", "College / institute"
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=150)
    level = models.CharField(max_length=20, choices=Level.choices, default=Level.SECONDARY)
    lecturer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, limit_choices_to={"role": "lecturer"},
        related_name="subjects_taught", help_text="Teacher who enters this subject's marks.")
    class Meta:
        ordering = ["level", "code"]
        verbose_name_plural = "subjects"
    def __str__(self): return f"{self.code} – {self.name}"


class TimetableEntry(models.Model):
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


class Programme(models.Model):
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
    code = models.CharField(max_length=20, unique=True)
    level = models.CharField(max_length=20, choices=Level.choices)
    duration_months = models.PositiveIntegerField(default=12)
    tuition_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    def __str__(self): return f"{self.name} ({self.get_level_display()})"


class Course(models.Model):
    code = models.CharField(max_length=20, unique=True)
    title = models.CharField(max_length=150)
    programme = models.ForeignKey(Programme, on_delete=models.CASCADE, related_name="courses")
    credits = models.PositiveSmallIntegerField(default=3)
    lecturer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, limit_choices_to={"role": "lecturer"}, related_name="courses")
    class Meta: ordering = ["code"]
    def __str__(self): return f"{self.code} – {self.title}"


class Student(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DEFERRED = "deferred", "Deferred"
        GRADUATED = "graduated", "Graduated"
        WITHDRAWN = "withdrawn", "Withdrawn"
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="student")
    student_no = models.CharField(max_length=20, unique=True, blank=True)
    programme = models.ForeignKey(Programme, on_delete=models.PROTECT, related_name="students")
    school_class = models.ForeignKey(SchoolClass, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="students")
    stream = models.ForeignKey(Stream, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="students")
    year_or_grade = models.CharField("Year / Grade", max_length=20, default="1")
    date_of_birth = models.DateField(null=True, blank=True)
    guardian_name = models.CharField(max_length=120, blank=True)
    guardian_phone = models.CharField(max_length=30, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    created = models.DateTimeField(auto_now_add=True)

    def save(self, *a, **kw):
        super().save(*a, **kw)
        if not self.student_no:
            self.student_no = f"{self.created.year}{self.pk:05d}"
            super().save(update_fields=["student_no"])

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
    def balance(self): return self.total_billed - self.total_paid - self.total_waived
    def __str__(self): return f"{self.student_no} {self.name}"


class Guardian(models.Model):
    """Links a parent account to a student so the parent can follow their progress."""
    parent = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                               related_name="wards")
    student = models.ForeignKey(Student, on_delete=models.CASCADE,
                                related_name="guardian_links")
    linked = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("parent", "student")
        ordering = ["student__user__last_name"]

    def __str__(self):
        return f"{self.parent.get_full_name() or self.parent.username} – {self.student.name}"


class GradeBand(models.Model):
    """One row per grade: a mark of min_mark or higher earns this letter and points."""
    letter = models.CharField(max_length=5)
    min_mark = models.DecimalField(max_digits=5, decimal_places=2,
                                   validators=[MinValueValidator(0)])
    points = models.DecimalField(max_digits=4, decimal_places=2, default=0)

    class Meta:
        ordering = ["-min_mark"]
        verbose_name_plural = "grade bands"

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
    def grade_for(cls, mark):
        """Return (letter, points) for a mark. Falls back to the default A–F scale."""
        if mark is None:
            return "—", None
        bands = list(cls.objects.order_by("-min_mark"))
        if not bands:
            bands = [cls(letter=l, min_mark=Decimal(m), points=Decimal(p))
                     for l, m, p in DEFAULT_GRADE_BANDS]
        for b in bands:
            if mark >= b.min_mark:
                return b.letter, b.points
        return bands[-1].letter, bands[-1].points

    def __str__(self): return f"{self.letter} from {self.min_mark:g}"


class Enrollment(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="enrollments")
    course = models.ForeignKey(Course, null=True, blank=True, on_delete=models.CASCADE,
        related_name="enrollments")
    subject = models.ForeignKey(Subject, null=True, blank=True, on_delete=models.CASCADE,
        related_name="enrollments")
    term = models.CharField(max_length=30, default="Term 1")
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
        keys = {(e.course_id, e.subject_id, e.term) for e in enrollments}
        if not keys:
            return {}
        query = Q()
        for course_id, subject_id, term in keys:
            query |= Q(course_id=course_id, subject_id=subject_id, term=term)
        rows = list(cls.objects.filter(mark__isnull=False).filter(query)
                    .select_related("student__user")
                    .order_by("-mark", "student__user__last_name",
                              "student__user__first_name", "pk"))
        totals = Counter((r.course_id, r.subject_id, r.term) for r in rows)
        seen, out = Counter(), {}
        for r in rows:
            key = (r.course_id, r.subject_id, r.term)
            seen[key] += 1
            out[r.pk] = (seen[key], totals[key])
        return out

    def __str__(self): return f"{self.student} – {self.label}"


class Activity(models.Model):
    class Kind(models.TextChoices):
        ASSIGN = "assignment", "Assignment"
        TEST = "test", "Test / Exam"
        CLUB = "club", "Club / Society"
        SPORT = "sport", "Sport"
        PROJECT = "project", "Project"
        DISCIPLINE = "discipline", "Discipline note"
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="activities")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    title = models.CharField(max_length=150)
    date = models.DateField()
    detail = models.CharField(max_length=250, blank=True)
    class Meta: ordering = ["-date"]


class Attendance(models.Model):
    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE, related_name="attendance")
    date = models.DateField()
    present = models.BooleanField(default=True)
    class Meta: unique_together = ("enrollment", "date")

    @classmethod
    def rates_by_term(cls, enrollments):
        """Return {term_label: (sessions, present)} for the given enrollments."""
        term_of = {e.pk: e.term for e in enrollments}
        out = {}
        for eid, present in cls.objects.filter(enrollment_id__in=term_of.keys()) \
                                       .values_list("enrollment_id", "present"):
            sessions, marked = out.setdefault(term_of[eid], [0, 0])
            out[term_of[eid]] = [sessions + 1, marked + (1 if present else 0)]
        return {t: (s, m) for t, (s, m) in out.items()}

    @classmethod
    def register(cls, enrollments):
        """Return (dates, {enrollment_pk: {date: present}}) for a class register."""
        rows = cls.objects.filter(enrollment_id__in=[e.pk for e in enrollments]) \
                          .values_list("enrollment_id", "date", "present")
        dates, grid = [], {}
        for eid, d, p in rows:
            if d not in dates:
                dates.append(d)
            grid.setdefault(eid, {})[d] = p
        return sorted(dates), grid


class Announcement(models.Model):
    title = models.CharField(max_length=150)
    body = models.TextField()
    audience = models.CharField(max_length=20, default="all",
        choices=[("all", "Everyone"), ("student", "Students"),
                 ("lecturer", "Lecturers"), ("parent", "Parents")])
    school_class = models.ForeignKey(SchoolClass, null=True, blank=True,
        on_delete=models.CASCADE, related_name="announcements",
        help_text="Optional: only this class sees the notice.")
    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="announcements")
    posted = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ["-posted"]

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
            return cls.objects.filter(audience__in=("all", "lecturer"))
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
        return cls.objects.none()


class ExamSitting(models.Model):
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
