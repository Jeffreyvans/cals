"""Read-mostly REST API v1.

Every viewset is tenant-scoped twice: the model's tenant manager filters on the
current institution (set by the API key), and the viewset filters explicitly
on the key's institution as a belt-and-braces check. Row-level rules (a student
may read only their own record, a parent only their linked children) are
applied in ``get_queryset`` as well.
"""

import csv

from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import filters, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

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
from accounts.models import User
from admissions.models import Application
from core.access import user_has_any_code, user_has_code
from core.numbering import number_key
from finance.models import Invoice, Payment

from .filters import SimpleFilterBackend
from .models import WebhookEndpoint
from .pagination import StandardPagination
from .permissions import HasApiPermission, IsInstitutionMember
from .serializers import (
    AcademicYearSerializer,
    ApplicationSerializer,
    AttendanceDeviceSerializer,
    AttendanceSerializer,
    CourseSerializer,
    EnrollmentSerializer,
    InvoiceSerializer,
    PaymentSerializer,
    ProgrammeSerializer,
    SchoolClassSerializer,
    StaffSerializer,
    StudentSerializer,
    SubjectSerializer,
    TermSerializer,
)


class ApiViewSet(viewsets.GenericViewSet):
    """Common plumbing: pagination, filtering, permissions and tenant scope."""

    permission_classes = [HasApiPermission, IsInstitutionMember]
    pagination_class = StandardPagination
    filter_backends = [SimpleFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    throttle_scope = "api"
    required_permissions = ()
    write_permissions = None

    def get_queryset(self):
        queryset = super().get_queryset()
        institution = getattr(self.request, "institution", None)
        if institution is None:
            return queryset.none()
        return queryset.filter(institution_id=institution.pk)


class ReadOnlyApiViewSet(ApiViewSet, viewsets.ReadOnlyModelViewSet):
    pass


# ---------- who am I ----------
class WhoAmIView(APIView):
    """The caller's account, institution and effective permission codes."""

    permission_classes = [HasApiPermission, IsInstitutionMember]
    throttle_scope = "api"

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        key = request.api_key
        codes = sorted(request.user.permission_codes())
        return Response(
            {
                "user": {
                    "id": request.user.pk,
                    "username": request.user.username,
                    "name": request.user.get_full_name() or request.user.username,
                    "role": request.user.role,
                },
                "institution": {
                    "id": request.institution.pk,
                    "slug": request.institution.slug,
                    "name": request.institution.name,
                    "currency": request.institution.currency,
                },
                "key": {
                    "name": key.name,
                    "prefix": key.prefix,
                    "scopes": key.scopes,
                    "last_used": key.last_used,
                },
                "permissions": [code for code in codes if key.permits(code)],
            }
        )


# ---------- academics ----------
class AcademicYearViewSet(ReadOnlyApiViewSet):
    queryset = AcademicYear.objects.all()
    serializer_class = AcademicYearSerializer
    required_permissions = ("academics.view_reports", "academics.manage_structure")
    filterset_fields = ["is_current", "is_closed"]
    ordering_fields = ["name", "start_date", "end_date"]
    ordering = ["-start_date"]
    search_fields = ["name"]


class TermViewSet(ReadOnlyApiViewSet):
    queryset = Term.objects.select_related("academic_year")
    serializer_class = TermSerializer
    required_permissions = ("academics.view_reports", "academics.manage_structure")
    filterset_fields = ["academic_year", "is_current", "is_closed"]
    ordering_fields = ["start_date", "end_date", "name"]
    ordering = ["-start_date"]
    search_fields = ["name"]


class ProgrammeViewSet(ReadOnlyApiViewSet):
    queryset = Programme.objects.all()
    serializer_class = ProgrammeSerializer
    required_permissions = ("academics.view_reports", "academics.manage_courses")
    filterset_fields = ["level"]
    ordering_fields = ["code", "name"]
    ordering = ["code"]
    search_fields = ["code", "name"]


class CourseViewSet(ReadOnlyApiViewSet):
    queryset = Course.objects.select_related("programme")
    serializer_class = CourseSerializer
    required_permissions = ("academics.view_reports", "academics.manage_courses")
    filterset_fields = ["programme", "lecturer"]
    ordering_fields = ["code", "title", "credits"]
    ordering = ["code"]
    search_fields = ["code", "title"]


class SubjectViewSet(ReadOnlyApiViewSet):
    queryset = Subject.objects.all()
    serializer_class = SubjectSerializer
    required_permissions = ("academics.view_reports", "academics.manage_structure")
    filterset_fields = ["level", "lecturer"]
    ordering_fields = ["code", "name"]
    ordering = ["code"]
    search_fields = ["code", "name"]


class SchoolClassViewSet(ReadOnlyApiViewSet):
    queryset = SchoolClass.objects.all()
    serializer_class = SchoolClassSerializer
    required_permissions = ("academics.view_reports", "academics.manage_structure")
    filterset_fields = ["level"]
    ordering_fields = ["name"]
    ordering = ["name"]
    search_fields = ["name"]


class StudentViewSet(ReadOnlyApiViewSet):
    """Students. A plain student sees only their own record; a parent only their children."""

    queryset = Student.objects.select_related("user", "programme", "school_class", "stream")
    serializer_class = StudentSerializer
    required_permissions = (
        "students.view_all",
        "students.view_own",
        "parent_portal.view_child",
    )
    filterset_fields = [
        "status",
        "programme",
        "school_class",
        "stream",
        "year_or_grade",
        "created",
        "student_no",
    ]
    ordering_fields = ["student_no", "created", "user__last_name", "year_or_grade"]
    ordering = ["student_no"]
    search_fields = ["student_no", "user__first_name", "user__last_name", "user__username"]

    def get_queryset(self):
        from django.db.models import Q

        queryset = super().get_queryset()
        user = self.request.user
        if user_has_code(user, "students.view_all"):
            return queryset
        scope = Q()
        if user_has_code(user, "students.view_own"):
            scope |= Q(user=user)
        if user_has_code(user, "parent_portal.view_child"):
            scope |= Q(guardian_links__parent=user)
        if not scope:
            return queryset.none()
        return queryset.filter(scope).distinct()


class StaffViewSet(ReadOnlyApiViewSet):
    """Staff accounts (not students or parents)."""

    queryset = User.objects.all()
    serializer_class = StaffSerializer
    required_permissions = ("admin.manage_users", "hr.manage")
    filterset_fields = ["role", "is_active", "staff_profile__job_title"]
    ordering_fields = ["username", "last_name", "date_joined"]
    ordering = ["last_name", "first_name"]
    search_fields = ["username", "first_name", "last_name", "email"]

    def get_queryset(self):
        queryset = User.objects.filter(
            institution_id=self.request.institution.pk
        ).select_related("staff_profile")
        return queryset.exclude(role__in=[User.Role.STUDENT, User.Role.PARENT])

class EnrollmentViewSet(ReadOnlyApiViewSet):
    queryset = Enrollment.objects.select_related("student__user", "course", "subject", "term")
    serializer_class = EnrollmentSerializer
    required_permissions = (
        "academics.view_reports",
        "students.view_all",
        "students.view_own",
        "parent_portal.view_child",
    )
    filterset_fields = ["student", "course", "subject", "term", "status", "offering"]
    ordering_fields = ["id", "mark", "student__student_no"]
    ordering = ["student__student_no", "id"]
    search_fields = ["student__student_no", "student__user__last_name", "course__code"]

    def get_queryset(self):
        from django.db.models import Q

        queryset = super().get_queryset()
        user = self.request.user
        if user_has_any_code(user, ("academics.view_reports", "students.view_all")):
            return queryset
        scope = Q()
        if user_has_code(user, "students.view_own"):
            scope |= Q(student__user=user)
        if user_has_code(user, "parent_portal.view_child"):
            scope |= Q(student__guardian_links__parent=user)
        if not scope:
            return queryset.none()
        return queryset.filter(scope).distinct()


class AttendanceViewSet(ReadOnlyApiViewSet):
    queryset = Attendance.objects.select_related(
        "enrollment__student__user", "enrollment__course", "enrollment__subject", "recorded_by"
    )
    serializer_class = AttendanceSerializer
    required_permissions = (
        "academics.view_reports",
        "academics.take_attendance",
        "students.view_own",
        "parent_portal.view_child",
    )
    filterset_fields = [
        "date",
        "status",
        "present",
        "enrollment",
        "enrollment__student",
        "enrollment__course",
    ]
    ordering_fields = ["date", "status"]
    ordering = ["-date"]

    def get_queryset(self):
        from django.db.models import Q

        queryset = super().get_queryset()
        user = self.request.user
        if user_has_any_code(user, ("academics.view_reports", "academics.take_attendance")):
            return queryset
        scope = Q()
        if user_has_code(user, "students.view_own"):
            scope |= Q(enrollment__student__user=user)
        if user_has_code(user, "parent_portal.view_child"):
            scope |= Q(enrollment__student__guardian_links__parent=user)
        if not scope:
            return queryset.none()
        return queryset.filter(scope).distinct()


# ---------- finance ----------
class InvoiceViewSet(ReadOnlyApiViewSet):
    queryset = Invoice.objects.select_related("student__user", "term")
    serializer_class = InvoiceSerializer
    required_permissions = (
        "finance.view_reports",
        "students.view_own",
        "parent_portal.view_finance",
    )
    filterset_fields = ["student", "term", "structure", "due_date", "created"]
    ordering_fields = ["due_date", "amount", "created"]
    ordering = ["-created"]

    def get_queryset(self):
        from django.db.models import Q

        queryset = super().get_queryset()
        user = self.request.user
        if user_has_code(user, "finance.view_reports"):
            return queryset
        scope = Q()
        if user_has_code(user, "students.view_own"):
            scope |= Q(student__user=user)
        if user_has_code(user, "parent_portal.view_finance"):
            scope |= Q(
                student__guardian_links__parent=user,
                student__guardian_links__can_view_finance=True,
            )
        if not scope:
            return queryset.none()
        return queryset.filter(scope).distinct()


class PaymentViewSet(ReadOnlyApiViewSet):
    queryset = Payment.objects.select_related(
        "invoice__student__user", "invoice__student", "received_by"
    )
    serializer_class = PaymentSerializer
    required_permissions = (
        "finance.view_reports",
        "students.view_own",
        "parent_portal.view_finance",
    )
    filterset_fields = ["invoice", "invoice__student", "method", "paid_on"]
    ordering_fields = ["paid_on", "amount"]
    ordering = ["-paid_on"]

    def get_queryset(self):
        from django.db.models import Q

        queryset = super().get_queryset()
        user = self.request.user
        if user_has_code(user, "finance.view_reports"):
            return queryset
        scope = Q()
        if user_has_code(user, "students.view_own"):
            scope |= Q(invoice__student__user=user)
        if user_has_code(user, "parent_portal.view_finance"):
            scope |= Q(
                invoice__student__guardian_links__parent=user,
                invoice__student__guardian_links__can_view_finance=True,
            )
        if not scope:
            return queryset.none()
        return queryset.filter(scope).distinct()


# ---------- admissions ----------
class ApplicationViewSet(ReadOnlyApiViewSet):
    queryset = Application.objects.select_related("programme", "school_class")
    serializer_class = ApplicationSerializer
    required_permissions = ("admissions.manage", "admissions.review")
    filterset_fields = ["status", "programme", "school_class", "eligible", "submitted_at"]
    ordering_fields = ["submitted_at", "last_name", "status"]
    ordering = ["-submitted_at"]
    search_fields = ["first_name", "last_name", "email", "reference", "phone"]


def _student_by_number(institution, number):
    """Find a student by number, tolerating case, spaces and separators.

    A device operator may key the number from a card or a printout, so
    ``stu/2026/0001`` reaches the same student as ``STU-2026-0001``.
    """
    key = number_key(number)
    if not key:
        return None
    for student in Student.all_objects.filter(institution=institution):
        if number_key(student.student_no) == key:
            return student
    return None


# ---------- integrations ----------
class AttendanceDeviceView(APIView):
    """Bulk attendance from a turnstile or other device.

    The device posts ``{"records": [{"student_no": "...", "date": "...",
    "status": "present|late|absent|excused", "reason": "..."}]}``. Each record
    updates the student's attendance for that date, creating or replacing the
    row. The response lists the written ids and any records that were skipped.
    """

    permission_classes = [HasApiPermission, IsInstitutionMember]
    throttle_scope = "api-write"
    required_permissions = ("academics.take_attendance",)

    @extend_schema(request=AttendanceDeviceSerializer, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        payload = AttendanceDeviceSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        institution = request.institution
        written = []
        skipped = []
        with transaction.atomic():
            for index, record in enumerate(payload.validated_data["records"]):
                student = (
                    _student_by_number(institution, record["student_no"])
                    if institution
                    else None
                )
                if student is None:
                    skipped.append({"index": index, "reason": "Unknown student number."})
                    continue
                enrollments = Enrollment.objects.filter(student=student)
                if record.get("course"):
                    enrollments = enrollments.filter(course__code=record["course"])
                if record.get("subject"):
                    enrollments = enrollments.filter(subject__code=record["subject"])
                enrollment = enrollments.order_by("-id").first()
                if enrollment is None:
                    skipped.append(
                        {"index": index, "reason": "The student has no enrolment to mark."}
                    )
                    continue
                attendance, _created = Attendance.objects.update_or_create(
                    enrollment=enrollment,
                    date=record["date"],
                    defaults={
                        "status": record["status"],
                        "reason": record.get("reason", ""),
                        "recorded_by": request.user,
                    },
                )
                written.append(attendance.pk)
        return Response(
            {
                "written": len(written),
                "ids": written,
                "skipped": skipped,
                "received_at": timezone.now(),
            }
        )


class WebhookPingView(APIView):
    """Queue a test event to every active endpoint of the caller's institution."""

    permission_classes = [HasApiPermission, IsInstitutionMember]
    throttle_scope = "api-write"
    required_permissions = ("admin.manage_settings",)

    @extend_schema(request=None, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        from .webhooks import dispatch_event

        endpoints = WebhookEndpoint.objects.filter(
            institution=request.institution, is_active=True
        )
        if not endpoints.exists():
            raise ValidationError("This institution has no active webhook endpoint.")
        deliveries = dispatch_event(
            "webhook.ping",
            {"message": "EduSphere webhook test", "requested_by": request.user.username},
            institution=request.institution,
        )
        return Response({"queued": len(deliveries)})


class JournalExportView(APIView):
    """Download the ledger as a CSV that an accounting package can import.

    One row per journal line, oldest first. ``?start=`` and ``?end=`` limit the
    entry dates. Amounts are plain decimals so a spreadsheet reads them as
    numbers.
    """

    permission_classes = [HasApiPermission, IsInstitutionMember]
    throttle_scope = "api"
    required_permissions = ("finance.view_ledger", "finance.view_reports")

    @extend_schema(responses={(200, "text/csv"): OpenApiTypes.STR})
    def get(self, request):
        from finance.models import JournalLine

        lines = JournalLine.objects.filter(institution=request.institution).select_related(
            "entry", "account"
        )
        start = request.query_params.get("start")
        end = request.query_params.get("end")
        if start:
            lines = lines.filter(entry__date__gte=start)
        if end:
            lines = lines.filter(entry__date__lte=end)
        lines = lines.order_by("entry__date", "entry_id", "id")

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="journal-export.csv"'
        writer = csv.writer(response)
        writer.writerow(
            [
                "date",
                "entry",
                "description",
                "reference",
                "account_code",
                "account_name",
                "debit",
                "credit",
            ]
        )
        for line in lines:
            writer.writerow(
                [
                    line.entry.date,
                    line.entry.number,
                    line.entry.description,
                    line.entry.reference,
                    line.account.code,
                    line.account.name,
                    line.debit,
                    line.credit,
                ]
            )
        return response
