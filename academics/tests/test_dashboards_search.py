from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from academics.models import (
    AcademicYear,
    Attendance,
    Course,
    Enrollment,
    Programme,
    Student,
    Term,
)

User = get_user_model()


class DashboardBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            "boss", password="pw123456", role="admin", is_staff=True,
            is_superuser=True, first_name="Bea", last_name="Boss")
        cls.programme = Programme.objects.create(
            name="Diploma in IT", code="DIT", level="diploma",
            tuition_fee=Decimal("100.00"))
        cls.year = AcademicYear.objects.create(
            name="2026", start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31), is_current=True)
        cls.term = Term.objects.create(
            academic_year=cls.year, name="Term 1",
            start_date=date(2026, 1, 12), end_date=date(2026, 4, 10),
            is_current=True)
        cls.course = Course.objects.create(
            code="IT101", title="Programming", programme=cls.programme,
            lecturer=cls.admin)
        user = User.objects.create_user(
            "risky", password="pw123456", role="student",
            first_name="Rita", last_name="Risk")
        cls.student = Student.objects.create(
            user=user, student_no="R100", status="active",
            year_or_grade="1", programme=cls.programme)
        cls.enrollment = Enrollment.objects.create(
            student=cls.student, course=cls.course, term=cls.term)
        cls.enrollment.mark = Decimal("30")
        cls.enrollment.save(update_fields=["mark"])
        for offset in range(4):
            Attendance.objects.create(
                enrollment=cls.enrollment, status=Attendance.Status.ABSENT,
                date=date(2026, 2, 2) + timedelta(days=offset))


class RoleDashboardTests(DashboardBase):
    def setUp(self):
        self.client.force_login(self.admin)

    def test_the_dashboard_renders_the_headline_figures(self):
        response = self.client.get(reverse("role_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Role dashboard")
        self.assertContains(response, "Collection rate")
        self.assertContains(response, "Collections, last six months")

    def test_low_attendance_and_low_marks_list_a_student_at_risk(self):
        response = self.client.get(reverse("role_dashboard"))
        self.assertContains(response, "At-risk students")
        self.assertContains(response, "Rita Risk")
        self.assertContains(response, "Attendance 0%")
        self.assertContains(response, "Average mark 30")

    def test_a_billed_student_is_counted_in_the_outstanding_total(self):
        from finance.models import Invoice

        Invoice.objects.create(
            student=self.student, description="Tuition", amount=Decimal("500.00"),
            due_date=date(2026, 2, 1), term=self.term)
        response = self.client.get(reverse("role_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "500")

    def test_a_student_cannot_open_the_role_dashboard(self):
        student_user = self.student.user
        self.client.force_login(student_user)
        response = self.client.get(reverse("role_dashboard"))
        self.assertEqual(response.status_code, 403)


class GlobalSearchTests(DashboardBase):
    def setUp(self):
        self.client.force_login(self.admin)

    def test_search_finds_a_student_and_a_course(self):
        response = self.client.get(reverse("global_search"), {"q": "Rita"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Rita Risk")
        self.assertContains(response, "Students")

        response = self.client.get(reverse("global_search"), {"q": "IT101"})
        self.assertContains(response, "Programming")
        self.assertContains(response, "Courses")

    def test_search_finds_a_number_typed_with_different_separators(self):
        self.student.student_no = "STU/2026/0007"
        self.student.save(update_fields=["student_no"])
        response = self.client.get(
            reverse("global_search"), {"q": "stu 2026 0007"})
        self.assertContains(response, "Rita Risk")

    def test_student_search_api_tolerates_separators(self):
        self.student.student_no = "STU-2026-0007"
        self.student.save(update_fields=["student_no"])
        response = self.client.get(
            reverse("student_search_api"), {"q": "stu/2026/0007"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["no"], "STU-2026-0007")

    def test_short_queries_do_not_search(self):
        response = self.client.get(reverse("global_search"), {"q": "R"})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Rita Risk")
        self.assertContains(response, "at least two characters")

    def test_a_student_cannot_use_global_search(self):
        self.client.force_login(self.student.user)
        response = self.client.get(reverse("global_search"), {"q": "Rita"})
        self.assertEqual(response.status_code, 403)
