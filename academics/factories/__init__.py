from datetime import date
from decimal import Decimal

import factory
from django.contrib.auth import get_user_model

from academics.models import (
    AcademicYear,
    Activity,
    Announcement,
    Attendance,
    Course,
    Enrollment,
    ExamSitting,
    GradeBand,
    Guardian,
    Programme,
    SchoolClass,
    SchoolSettings,
    Stream,
    Student,
    Subject,
    Term,
    TimetableEntry,
)

User = get_user_model()


class ProgrammeFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Programme
        django_get_or_create = ("code",)

    name = factory.Sequence(lambda n: f"Programme {n}")
    code = factory.Sequence(lambda n: f"P{n:03d}")
    level = "diploma"
    duration_months = 12
    tuition_fee = Decimal("500.00")


class AcademicYearFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = AcademicYear
        django_get_or_create = ("name",)

    name = factory.Sequence(lambda n: f"202{n}")
    start_date = date(2026, 1, 1)
    end_date = date(2026, 12, 31)
    is_current = False


class TermFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Term
        django_get_or_create = ("academic_year", "name")

    academic_year = factory.SubFactory(AcademicYearFactory)
    name = "Term 1"
    start_date = date(2026, 1, 1)
    end_date = date(2026, 4, 1)
    is_current = False


class SchoolClassFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SchoolClass
        django_get_or_create = ("name",)

    name = factory.Sequence(lambda n: f"Form {n}")
    level = "secondary"


class StreamFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Stream

    school_class = factory.SubFactory(SchoolClassFactory)
    name = "A"


class SubjectFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Subject
        django_get_or_create = ("code",)

    code = factory.Sequence(lambda n: f"SUB{n:03d}")
    name = factory.Sequence(lambda n: f"Subject {n}")
    level = "secondary"


class CourseFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Course
        django_get_or_create = ("code",)

    code = factory.Sequence(lambda n: f"CRS{n:03d}")
    title = factory.Sequence(lambda n: f"Course {n}")
    programme = factory.SubFactory(ProgrammeFactory)
    credits = 3


class StudentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Student
        django_get_or_create = ("user",)

    user = factory.SubFactory("accounts.factories.UserFactory", role="student")
    programme = factory.SubFactory(ProgrammeFactory)
    year_or_grade = "1"
    status = "active"


class GuardianFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Guardian
        django_get_or_create = ("parent", "student")

    parent = factory.SubFactory("accounts.factories.UserFactory", role="parent")
    student = factory.SubFactory(StudentFactory)


class EnrollmentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Enrollment

    student = factory.SubFactory(StudentFactory)
    course = factory.SubFactory(CourseFactory)
    term = "Term 1"


class ActivityFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Activity

    student = factory.SubFactory(StudentFactory)
    kind = "assignment"
    title = factory.Sequence(lambda n: f"Activity {n}")
    date = date.today()
    detail = ""


class AnnouncementFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Announcement

    title = factory.Sequence(lambda n: f"Notice {n}")
    body = "Notice body"
    audience = "all"


class AttendanceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Attendance

    enrollment = factory.SubFactory(EnrollmentFactory)
    date = date.today()
    present = True


class GradeBandFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = GradeBand
        django_get_or_create = ("letter",)

    letter = "A"
    min_mark = Decimal("75")
    points = Decimal("4")


class ExamSittingFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ExamSitting

    student = factory.SubFactory(StudentFactory)
    term = factory.SubFactory(TermFactory)
    exam_no = factory.Sequence(lambda n: f"EX-2026-{n:04d}")
    venue = "Hall"
    cleared = True


class TimetableEntryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TimetableEntry

    academic_year = factory.SubFactory(AcademicYearFactory)
    term = factory.SubFactory(TermFactory)
    day = 1
    start_time = factory.LazyFunction(lambda: __import__("datetime").time(8, 0))
    end_time = factory.LazyFunction(lambda: __import__("datetime").time(9, 0))
    course = factory.SubFactory(CourseFactory)
    lecturer = factory.SubFactory("accounts.factories.UserFactory", role="lecturer")


class SchoolSettingsFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SchoolSettings

    mode = "college"
