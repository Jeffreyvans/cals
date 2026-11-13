"""Phase 8: the REST API - keys, tenancy, permissions, filtering and docs."""

import json
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from academics.models import (
    AcademicYear,
    Attendance,
    Course,
    Enrollment,
    Guardian,
    Programme,
    Student,
    Term,
)
from api.models import ApiKey
from core.models import Institution
from core.roles import ensure_roles
from core.tenancy import use_institution
from finance.models import Invoice, Payment

User = get_user_model()


def auth_header(raw_key):
    return {"HTTP_AUTHORIZATION": f"Api-Key {raw_key}"}


class ApiTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.institution = Institution.objects.create(
            name="API College", slug="api-college", type="college"
        )
        with use_institution(cls.institution):
            ensure_roles(cls.institution)
            cls.programme = Programme.objects.create(
                name="Diploma in IT", code="DIT", level="diploma", tuition_fee=500
            )
            cls.year = AcademicYear.objects.create(
                name="2026", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
                is_current=True,
            )
            cls.term = Term.objects.create(
                academic_year=cls.year, name="Term 1",
                start_date=date(2026, 1, 1), end_date=date(2026, 4, 1), is_current=True,
            )
            cls.course = Course.objects.create(
                code="C1", title="Programming", programme=cls.programme, credits=3
            )
            cls.admin = User.objects.create_user(
                "apiadmin", password="pw123456", role="admin", institution=cls.institution
            )
            cls.bursar = User.objects.create_user(
                "apibursar", password="pw123456", role="bursar", institution=cls.institution
            )
            cls.student_user = User.objects.create_user(
                "student1", password="pw123456", role="student", institution=cls.institution
            )
            cls.student = Student.objects.create(
                user=cls.student_user, programme=cls.programme, student_no="S100",
                institution=cls.institution,
            )
            cls.other_student_user = User.objects.create_user(
                "student2", password="pw123456", role="student", institution=cls.institution
            )
            cls.other_student = Student.objects.create(
                user=cls.other_student_user, programme=cls.programme, student_no="S200",
                institution=cls.institution,
            )
            cls.parent_user = User.objects.create_user(
                "parent1", password="pw123456", role="parent", institution=cls.institution
            )
            Guardian.objects.create(parent=cls.parent_user, student=cls.student)
            cls.enrolment = Enrollment.objects.create(
                student=cls.student, course=cls.course, term=cls.term,
                institution=cls.institution,
            )
            cls.invoice = Invoice.objects.create(
                student=cls.student, description="Tuition", amount=100,
                due_date=date(2026, 3, 1), institution=cls.institution,
            )
            cls.other_invoice = Invoice.objects.create(
                student=cls.other_student, description="Tuition", amount=200,
                due_date=date(2026, 3, 1), institution=cls.institution,
            )
            cls.payment = Payment.objects.create(
                invoice=cls.invoice, amount=50, method="cash", received_by=cls.bursar,
                institution=cls.institution,
            )
            cls.admin_key, cls.admin_raw = ApiKey.issue(
                cls.institution, cls.admin, "Admin key"
            )
            cls.student_key, cls.student_raw = ApiKey.issue(
                cls.institution, cls.student_user, "Student key"
            )
            cls.parent_key, cls.parent_raw = ApiKey.issue(
                cls.institution, cls.parent_user, "Parent key"
            )
            cls.bursar_key, cls.bursar_raw = ApiKey.issue(
                cls.institution, cls.bursar, "Bursar key"
            )


class AuthenticationTests(ApiTestCase):
    def test_whoami_returns_the_account_and_tenant(self):
        response = self.client.get(reverse("api_whoami"), **auth_header(self.admin_raw))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["user"]["username"], "apiadmin")
        self.assertEqual(body["institution"]["slug"], "api-college")
        self.assertIn("admin.manage_settings", body["permissions"])
        self.assertEqual(body["key"]["name"], "Admin key")

    def test_a_missing_key_is_refused(self):
        response = self.client.get(reverse("api_whoami"))
        self.assertEqual(response.status_code, 401)

    def test_a_bad_secret_is_refused(self):
        prefix = self.admin_raw.split("_")[1]
        response = self.client.get(
            reverse("api_whoami"),
            **auth_header(f"esk_{prefix}_not-the-secret"),
        )
        self.assertEqual(response.status_code, 401)

    def test_a_revoked_key_is_refused(self):
        self.admin_key.is_active = False
        self.admin_key.save(update_fields=["is_active"])
        response = self.client.get(reverse("api_whoami"), **auth_header(self.admin_raw))
        self.assertEqual(response.status_code, 401)

    def test_an_expired_user_key_is_refused(self):
        User.objects.filter(pk=self.student_user.pk).update(is_active=False)
        response = self.client.get(reverse("api_whoami"), **auth_header(self.student_raw))
        self.assertEqual(response.status_code, 401)

    def test_a_key_with_narrower_scopes_loses_the_rest(self):
        _key, raw = ApiKey.issue(
            self.institution, self.admin, "Read-only invoices", ["finance.view_reports"]
        )
        whoami = self.client.get(reverse("api_whoami"), **auth_header(raw)).json()
        self.assertEqual(whoami["permissions"], ["finance.view_reports"])
        invoices = self.client.get(reverse("invoice-list"), **auth_header(raw))
        self.assertEqual(invoices.status_code, 200)
        students = self.client.get(reverse("student-list"), **auth_header(raw))
        self.assertEqual(students.status_code, 403)

    def test_queries_on_another_tenant_return_nothing(self):
        other = Institution.objects.create(name="Other", slug="other", type="college")
        with use_institution(other):
            programme = Programme.objects.create(
                name="Other Programme", code="OP", level="diploma", tuition_fee=1
            )
            user = User.objects.create_user("other_stu", password="pw", role="student",
                                            institution=other)
            Student.objects.create(
                user=user, programme=programme, student_no="S999", institution=other
            )
        response = self.client.get(reverse("student-list"), **auth_header(self.admin_raw))
        self.assertEqual(response.status_code, 200)
        numbers = [row["student_no"] for row in response.json()["results"]]
        self.assertIn("S100", numbers)
        self.assertNotIn("S999", numbers)


class RowScopeTests(ApiTestCase):
    def test_a_student_sees_only_their_own_record(self):
        response = self.client.get(reverse("student-list"), **auth_header(self.student_raw))
        self.assertEqual(response.status_code, 200)
        numbers = [row["student_no"] for row in response.json()["results"]]
        self.assertEqual(numbers, ["S100"])

    def test_a_student_sees_only_their_own_invoices(self):
        response = self.client.get(reverse("invoice-list"), **auth_header(self.student_raw))
        self.assertEqual(response.status_code, 200)
        rows = response.json()["results"]
        self.assertEqual([row["student_no"] for row in rows], ["S100"])

    def test_a_parent_sees_the_linked_child(self):
        response = self.client.get(reverse("student-list"), **auth_header(self.parent_raw))
        self.assertEqual(response.status_code, 200)
        numbers = [row["student_no"] for row in response.json()["results"]]
        self.assertIn("S100", numbers)
        self.assertNotIn("S200", numbers)

    def test_a_parent_without_finance_consent_sees_no_invoice(self):
        with use_institution(self.institution):
            Guardian.objects.filter(parent=self.parent_user).update(can_view_finance=False)
        response = self.client.get(reverse("invoice-list"), **auth_header(self.parent_raw))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], [])


class ListFeatureTests(ApiTestCase):
    def test_search_filter_and_ordering(self):
        response = self.client.get(
            reverse("student-list") + "?search=student2&ordering=-student_no",
            **auth_header(self.admin_raw),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["student_no"], "S200")

    def test_field_filters_and_in_lookups(self):
        url = reverse("student-list") + "?status__in=active,enrolled&student_no__contains=S1"
        response = self.client.get(url, **auth_header(self.admin_raw))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["student_no"] for row in response.json()["results"]], ["S100"])

    def test_unknown_filter_fields_are_ignored(self):
        url = reverse("student-list") + "?user__password=pbkdf2"
        response = self.client.get(url, **auth_header(self.admin_raw))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 2)

    def test_pagination(self):
        url = reverse("student-list") + "?page_size=1"
        response = self.client.get(url, **auth_header(self.admin_raw))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["results"]), 1)
        self.assertEqual(response.json()["count"], 2)

    def test_related_fields_are_flattened_for_invoices(self):
        response = self.client.get(reverse("invoice-list"), **auth_header(self.bursar_raw))
        self.assertEqual(response.status_code, 200)
        row = next(r for r in response.json()["results"] if r["student_no"] == "S100")
        self.assertEqual(row["student_name"], "student1")
        self.assertEqual(str(row["paid"]), "50.00")

    def test_attendance_list_for_a_student(self):
        Attendance.objects.create(
            enrollment=self.enrolment, date=date(2026, 2, 1), status="present",
            institution=self.institution,
        )
        response = self.client.get(
            reverse("attendance-list"), **auth_header(self.student_raw)
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["status"], "present")


class JournalExportTests(ApiTestCase):
    def test_the_bursar_downloads_the_ledger_as_csv(self):
        response = self.client.get(
            reverse("api_journal_export"), **auth_header(self.bursar_raw)
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("text/csv"))
        body = response.content.decode()
        self.assertIn("account_code", body)
        self.assertIn("Tuition", body)

    def test_the_date_range_is_applied(self):
        response = self.client.get(
            reverse("api_journal_export") + "?end=2000-01-01",
            **auth_header(self.bursar_raw),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.content.decode().strip().splitlines()), 1)

    def test_a_student_key_cannot_read_the_ledger(self):
        response = self.client.get(
            reverse("api_journal_export"), **auth_header(self.student_raw)
        )
        self.assertEqual(response.status_code, 403)


class SchemaTests(ApiTestCase):
    def test_the_openapi_schema_is_served(self):
        response = self.client.get(reverse("api_schema"), HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 200)
        schema = json.loads(response.content)
        self.assertEqual(schema["info"]["title"], "EduSphere API")
        self.assertIn("/api/v1/students/", schema["paths"])
        self.assertIn("ApiKeyAuth", schema["components"]["securitySchemes"])

    def test_the_swagger_page_is_served(self):
        response = self.client.get(reverse("api_docs"))
        self.assertEqual(response.status_code, 200)


class AttendanceDeviceTests(ApiTestCase):
    def setUp(self):
        self.url = reverse("api_attendance_device")

    def test_a_device_writes_and_updates_attendance(self):
        payload = {
            "records": [
                {"student_no": "S100", "date": "2026-02-02", "status": "present"},
                {"student_no": "S100", "date": "2026-02-03", "status": "absent",
                 "reason": "Sick"},
            ]
        }
        response = self.client.post(
            self.url, payload, content_type="application/json", **auth_header(self.admin_raw)
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["written"], 2)
        self.assertEqual(Attendance.all_objects.count(), 2)

        update = {
            "records": [{"student_no": "S100", "date": "2026-02-02", "status": "late"}]
        }
        response = self.client.post(
            self.url, update, content_type="application/json", **auth_header(self.admin_raw)
        )
        self.assertEqual(response.status_code, 200)
        row = Attendance.all_objects.get(date=date(2026, 2, 2))
        self.assertEqual(row.status, "late")

    def test_unknown_students_are_skipped(self):
        payload = {"records": [{"student_no": "NOPE", "date": "2026-02-02", "status": "present"}]}
        response = self.client.post(
            self.url, payload, content_type="application/json", **auth_header(self.admin_raw)
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["written"], 0)
        self.assertEqual(len(response.json()["skipped"]), 1)

    def test_a_student_key_cannot_write_attendance(self):
        payload = {"records": [{"student_no": "S100", "date": "2026-02-02", "status": "present"}]}
        response = self.client.post(
            self.url, payload, content_type="application/json",
            **auth_header(self.student_raw),
        )
        self.assertEqual(response.status_code, 403)
