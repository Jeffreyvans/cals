"""Serializers for the public API.

The API is deliberately read-mostly. Every serializer builds its output from
a tenant-scoped queryset, so a serializer never has to filter by institution
itself; the viewset does that before serialisation.
"""

from rest_framework import serializers

from academics.models import (
    AcademicYear,
    Attendance,
    Course,
    Enrollment,
    Programme,
    SchoolClass,
    Student,
    Subject,
    Term,
)
from admissions.models import Application
from finance.models import Invoice, Payment


class AcademicYearSerializer(serializers.ModelSerializer):
    class Meta:
        model = AcademicYear
        fields = ["id", "name", "start_date", "end_date", "is_current", "is_closed"]


class TermSerializer(serializers.ModelSerializer):
    academic_year = serializers.IntegerField(source="academic_year_id", read_only=True)
    academic_year_name = serializers.CharField(source="academic_year.name", read_only=True)

    class Meta:
        model = Term
        fields = [
            "id",
            "name",
            "academic_year",
            "academic_year_name",
            "start_date",
            "end_date",
            "is_current",
            "is_closed",
        ]


class ProgrammeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Programme
        fields = ["id", "code", "name", "level", "duration_months", "tuition_fee"]


class CourseSerializer(serializers.ModelSerializer):
    programme = serializers.IntegerField(source="programme_id", read_only=True)
    programme_name = serializers.CharField(source="programme.name", read_only=True)

    class Meta:
        model = Course
        fields = ["id", "code", "title", "programme", "programme_name", "credits"]


class SubjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subject
        fields = ["id", "code", "name", "level"]


class SchoolClassSerializer(serializers.ModelSerializer):
    class Meta:
        model = SchoolClass
        fields = ["id", "name", "level"]


class StudentSerializer(serializers.ModelSerializer):
    name = serializers.CharField(read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    phone = serializers.CharField(source="user.phone", read_only=True)
    programme = serializers.IntegerField(source="programme_id", read_only=True)
    programme_name = serializers.CharField(source="programme.name", read_only=True)
    programme_year = serializers.IntegerField(source="programme_year_id", read_only=True)
    cohort = serializers.IntegerField(source="cohort_id", read_only=True)
    school_class = serializers.IntegerField(source="school_class_id", read_only=True)
    school_class_name = serializers.CharField(source="school_class.name", read_only=True)
    stream = serializers.IntegerField(source="stream_id", read_only=True)
    class_label = serializers.CharField(read_only=True)

    class Meta:
        model = Student
        fields = [
            "id",
            "student_no",
            "name",
            "username",
            "email",
            "phone",
            "programme",
            "programme_name",
            "programme_year",
            "cohort",
            "school_class",
            "school_class_name",
            "stream",
            "class_label",
            "year_or_grade",
            "date_of_birth",
            "guardian_name",
            "guardian_phone",
            "status",
            "created",
        ]


class StaffSerializer(serializers.Serializer):
    """A staff account. Read-only; account changes go through the admin screens."""

    id = serializers.IntegerField(read_only=True)
    username = serializers.CharField(read_only=True)
    first_name = serializers.CharField(read_only=True)
    last_name = serializers.CharField(read_only=True)
    full_name = serializers.SerializerMethodField()
    email = serializers.EmailField(read_only=True)
    phone = serializers.CharField(read_only=True)
    role = serializers.CharField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    date_joined = serializers.DateTimeField(read_only=True)
    job_title = serializers.SerializerMethodField()

    def get_full_name(self, obj) -> str:
        return obj.get_full_name() or obj.username

    def get_job_title(self, obj) -> str:
        profile = getattr(obj, "staff_profile", None)
        return getattr(profile, "job_title", "") if profile else ""


class EnrollmentSerializer(serializers.ModelSerializer):
    student = serializers.IntegerField(source="student_id", read_only=True)
    student_no = serializers.CharField(source="student.student_no", read_only=True)
    student_name = serializers.CharField(source="student.name", read_only=True)
    course = serializers.IntegerField(source="course_id", read_only=True)
    course_title = serializers.CharField(source="course.title", read_only=True)
    subject = serializers.IntegerField(source="subject_id", read_only=True)
    subject_name = serializers.CharField(source="subject.name", read_only=True)
    term = serializers.IntegerField(source="term_id", read_only=True)
    term_name = serializers.CharField(source="term.name", read_only=True)

    class Meta:
        model = Enrollment
        fields = [
            "id",
            "student",
            "student_no",
            "student_name",
            "course",
            "course_title",
            "subject",
            "subject_name",
            "term",
            "term_name",
            "status",
            "mark",
        ]


class AttendanceSerializer(serializers.ModelSerializer):
    student = serializers.IntegerField(source="enrollment.student_id", read_only=True)
    student_no = serializers.CharField(source="enrollment.student.student_no", read_only=True)
    student_name = serializers.CharField(source="enrollment.student.name", read_only=True)
    course = serializers.IntegerField(source="enrollment.course_id", read_only=True)
    subject = serializers.IntegerField(source="enrollment.subject_id", read_only=True)
    recorded_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Attendance
        fields = [
            "id",
            "enrollment",
            "student",
            "student_no",
            "student_name",
            "course",
            "subject",
            "date",
            "status",
            "present",
            "reason",
            "recorded_by",
            "recorded_by_name",
            "guardians_notified",
        ]

    def get_recorded_by_name(self, obj):
        if not obj.recorded_by_id:
            return ""
        return obj.recorded_by.get_full_name() or obj.recorded_by.username


class InvoiceSerializer(serializers.ModelSerializer):
    student = serializers.IntegerField(source="student_id", read_only=True)
    student_no = serializers.CharField(source="student.student_no", read_only=True)
    student_name = serializers.CharField(source="student.name", read_only=True)
    term = serializers.IntegerField(source="term_id", read_only=True)
    paid = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    waived = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    discounted = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    balance = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id",
            "student",
            "student_no",
            "student_name",
            "description",
            "amount",
            "due_date",
            "term",
            "paid",
            "waived",
            "discounted",
            "balance",
            "created",
        ]


class PaymentSerializer(serializers.ModelSerializer):
    invoice = serializers.IntegerField(source="invoice_id", read_only=True)
    student = serializers.IntegerField(source="invoice.student_id", read_only=True)
    student_no = serializers.CharField(source="invoice.student.student_no", read_only=True)
    student_name = serializers.CharField(source="invoice.student.name", read_only=True)
    received_by_name = serializers.CharField(read_only=True)

    class Meta:
        model = Payment
        fields = [
            "id",
            "receipt_no",
            "invoice",
            "student",
            "student_no",
            "student_name",
            "amount",
            "method",
            "reference",
            "payer_name",
            "currency",
            "exchange_rate",
            "base_amount",
            "received_by",
            "received_by_name",
            "paid_on",
        ]


class ApplicationSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    programme = serializers.IntegerField(source="programme_id", read_only=True)
    programme_name = serializers.CharField(source="programme.name", read_only=True)

    class Meta:
        model = Application
        fields = [
            "id",
            "reference",
            "first_name",
            "last_name",
            "full_name",
            "date_of_birth",
            "gender",
            "email",
            "phone",
            "programme",
            "programme_name",
            "school_class",
            "year_or_grade",
            "status",
            "eligible",
            "interview_at",
            "submitted_at",
        ]

    def get_full_name(self, obj):
        return f"{obj.first_name} {obj.last_name}".strip()


class AttendanceSubmissionSerializer(serializers.Serializer):
    """The payload an attendance device or integration posts."""

    student_no = serializers.CharField()
    date = serializers.DateField()
    status = serializers.ChoiceField(choices=Attendance.Status.choices)
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    course = serializers.CharField(required=False, allow_blank=True, default="")
    subject = serializers.CharField(required=False, allow_blank=True, default="")


class AttendanceDeviceSerializer(serializers.Serializer):
    records = AttendanceSubmissionSerializer(many=True)
