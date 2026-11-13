"""Phase 5: parent, lecturer and department portals, and at-risk scoring."""

from datetime import date, timedelta
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from academics.models import (
    AcademicYear,
    Activity,
    AssignmentSubmission,
    AtRiskScore,
    Attendance,
    ClassTeacher,
    Course,
    Enrollment,
    Guardian,
    Programme,
    SchoolClass,
    Student,
    Term,
    TimetableEntry,
)
from accounts.models import User
from core.models import Department, Notification, StaffProfile
from finance.models import Invoice


class Phase5Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            "boss5", password="pw123456", role="admin",
            is_staff=True, is_superuser=True, first_name="Bea", last_name="Boss")
        cls.lecturer = User.objects.create_user(
            "lec5", password="pw123456", role="lecturer",
            first_name="Les", last_name="Lecturer")
        cls.parent = User.objects.create_user(
            "parent5", password="pw123456", role="parent",
            first_name="Pat", last_name="Parent")
        cls.other_parent = User.objects.create_user(
            "parent6", password="pw123456", role="parent")
        cls.programme = Programme.objects.create(
            name="Diploma in IT", code="DIT5", level="diploma",
            tuition_fee=Decimal("100.00"))
        cls.year = AcademicYear.objects.create(
            name="2026", start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31), is_current=True)
        cls.term = Term.objects.create(
            academic_year=cls.year, name="Term 1",
            start_date=date(2026, 1, 12), end_date=date(2026, 4, 10),
            is_current=True)
        cls.course = Course.objects.create(
            code="IT501", title="Web Development", programme=cls.programme,
            lecturer=cls.lecturer)
        cls.student_user = User.objects.create_user(
            "stu5", password="pw123456", role="student",
            first_name="Sam", last_name="Student")
        cls.student = Student.objects.create(
            user=cls.student_user, student_no="S500", status="active",
            year_or_grade="1", programme=cls.programme)
        cls.enrollment = Enrollment.objects.create(
            student=cls.student, course=cls.course, term=cls.term)
        cls.other_user = User.objects.create_user(
            "stu6", password="pw123456", role="student",
            first_name="Other", last_name="Student")
        cls.other_student = Student.objects.create(
            user=cls.other_user, student_no="S501", status="active",
            year_or_grade="1", programme=cls.programme)


class LecturerAssignmentTests(Phase5Base):
    def setUp(self):
        self.client.force_login(self.lecturer)

    def test_lecturer_sets_an_assignment_for_the_whole_course(self):
        response = self.client.post(reverse("assignment_create"), {
            "course": self.course.pk,
            "title": "Essay on markets",
            "due_date": timezone.localdate().isoformat(),
            "detail": "Two pages.",
        })
        self.assertRedirects(response, reverse("lecturer_assignments"))
        activities = Activity.objects.filter(
            title="Essay on markets", created_by=self.lecturer)
        self.assertEqual(activities.count(), 1)
        self.assertEqual(activities.first().student, self.student)
        self.assertEqual(activities.first().kind, Activity.Kind.ASSIGN)

    def test_lecturer_portal_lists_the_timetable_and_assignments(self):
        TimetableEntry.objects.create(
            academic_year=self.year, term=self.term, day=1,
            start_time="08:00", end_time="09:00", course=self.course,
            lecturer=self.lecturer, venue="Room 4")
        Activity.objects.create(
            student=self.student, course=self.course, kind="assignment",
            title="Essay on markets", date=timezone.localdate(),
            due_date=timezone.localdate(), created_by=self.lecturer)
        response = self.client.get(reverse("lecturer_portal"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Web Development")
        self.assertContains(response, "Essay on markets")
        self.assertContains(response, "Room 4")

    def test_assignment_detail_shows_what_students_submitted(self):
        activity = Activity.objects.create(
            student=self.student, course=self.course, kind="assignment",
            title="Essay on markets", date=timezone.localdate(),
            due_date=timezone.localdate(), created_by=self.lecturer)
        AssignmentSubmission.objects.create(
            institution=activity.institution, assignment=activity,
            student=self.student, text="First draft")
        response = self.client.get(
            reverse("assignment_detail", args=[activity.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "First draft")
        self.assertContains(response, "Sam Student")

    def test_lecturer_cannot_open_another_lecturers_assignment(self):
        other = User.objects.create_user(
            "lec6", password="pw123456", role="lecturer")
        activity = Activity.objects.create(
            student=self.student, course=self.course, kind="assignment",
            title="Not mine", date=timezone.localdate(),
            due_date=timezone.localdate(), created_by=other)
        response = self.client.get(
            reverse("assignment_detail", args=[activity.pk]))
        self.assertEqual(response.status_code, 404)


class ParentFinanceTests(Phase5Base):
    def setUp(self):
        self.invoice = Invoice.objects.create(
            student=self.student, description="Tuition Term 1",
            amount=Decimal("100.00"), due_date=timezone.localdate())
        self.other_invoice = Invoice.objects.create(
            student=self.other_student, description="Other tuition",
            amount=Decimal("50.00"), due_date=timezone.localdate())

    def test_parent_without_finance_consent_does_not_see_fees(self):
        Guardian.objects.create(
            parent=self.parent, student=self.student, can_view_finance=False)
        self.client.force_login(self.parent)
        response = self.client.get(
            reverse("child_detail", args=[self.student.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Fees owed")
        self.assertNotContains(response, "Tuition Term 1")
        self.assertNotContains(response, "Pay online")

    def test_parent_with_finance_consent_sees_fees_and_a_pay_link(self):
        Guardian.objects.create(
            parent=self.parent, student=self.student, can_view_finance=True)
        self.client.force_login(self.parent)
        response = self.client.get(
            reverse("child_detail", args=[self.student.pk]))
        self.assertContains(response, "Fees owed")
        self.assertContains(response, "Tuition Term 1")
        self.assertContains(response, "Pay online")

    def test_parent_can_open_online_payment_for_a_linked_child(self):
        Guardian.objects.create(
            parent=self.parent, student=self.student, can_view_finance=True)
        self.client.force_login(self.parent)
        response = self.client.get(
            reverse("pay_online", args=[self.invoice.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pay online")

    def test_parent_cannot_pay_for_an_unlinked_child(self):
        Guardian.objects.create(
            parent=self.parent, student=self.student, can_view_finance=True)
        self.client.force_login(self.parent)
        response = self.client.get(
            reverse("pay_online", args=[self.other_invoice.pk]))
        self.assertEqual(response.status_code, 403)

    def test_parent_without_finance_consent_cannot_pay_online(self):
        Guardian.objects.create(
            parent=self.parent, student=self.student, can_view_finance=False)
        self.client.force_login(self.parent)
        response = self.client.get(
            reverse("pay_online", args=[self.invoice.pk]))
        self.assertEqual(response.status_code, 403)

    def test_student_can_pay_their_own_invoice_only(self):
        self.client.force_login(self.student_user)
        self.assertEqual(self.client.get(
            reverse("pay_online", args=[self.invoice.pk])).status_code, 200)
        self.assertEqual(self.client.get(
            reverse("pay_online", args=[self.other_invoice.pk])).status_code, 403)


class DepartmentPortalTests(Phase5Base):
    def setUp(self):
        self.department = Department.objects.create(
            name="Computing", code="COMP5", head=self.lecturer)
        self.profile = StaffProfile.objects.create(
            user=self.lecturer, job_title="Lecturer")
        self.profile.departments.add(self.department)
        self.client.force_login(self.lecturer)

    def test_staff_sees_the_departments_they_belong_to(self):
        response = self.client.get(reverse("department_portal"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Computing")
        self.assertContains(response, "Les Lecturer")

    def test_students_cannot_open_the_department_portal(self):
        self.client.force_login(self.student_user)
        response = self.client.get(reverse("department_portal"))
        self.assertEqual(response.status_code, 403)


class AtRiskCommandTests(Phase5Base):
    def setUp(self):
        self.school_class = SchoolClass.objects.create(
            name="Form 5", level="secondary")
        self.student.school_class = self.school_class
        self.student.save(update_fields=["school_class"])
        ClassTeacher.objects.create(
            school_class=self.school_class, academic_year=self.year,
            teacher=self.lecturer)
        self.enrollment.mark = Decimal("30")
        self.enrollment.save(update_fields=["mark"])
        for offset in range(4):
            Attendance.objects.create(
                enrollment=self.enrollment, status=Attendance.Status.ABSENT,
                date=date(2026, 2, 2) + timedelta(days=offset))

    def test_the_nightly_run_scores_students_and_notifies_class_teachers(self):
        call_command("score_at_risk", "--institution=default")
        score = AtRiskScore.objects.get(student=self.student)
        self.assertEqual(score.score, 2)
        self.assertTrue(any("Average mark 30" in reason for reason in score.reasons))
        self.assertTrue(
            Notification.objects.filter(
                user=self.lecturer, category="at_risk",
                title__contains="Sam Student").exists())

    def test_a_dry_run_writes_nothing(self):
        call_command("score_at_risk", "--institution=default", "--dry-run")
        self.assertFalse(AtRiskScore.objects.filter(student=self.student).exists())
        self.assertFalse(
            Notification.objects.filter(user=self.lecturer, category="at_risk").exists())
