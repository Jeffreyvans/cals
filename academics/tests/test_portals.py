from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from academics.models import Activity, AssignmentSubmission, Programme, Student
from admissions.models import StudentCard

User = get_user_model()


class StudentPortalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="stu1", password="pw123456", role="student",
            first_name="Sam", last_name="Student")
        self.programme = Programme.objects.create(
            name="Diploma", code="DIP", level="diploma",
            tuition_fee=Decimal("100.00"))
        self.student = Student.objects.create(
            user=self.user, student_no="S1", status="enrolled",
            year_or_grade="1", programme=self.programme)
        self.client.force_login(self.user)

    def test_student_portal_lists_their_assignments(self):
        Activity.objects.create(
            student=self.student, kind="assignment", title="Essay on markets",
            date=date.today(), due_date=date.today(), detail="Two pages.")
        response = self.client.get(reverse("student_portal"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Essay on markets")

    def test_student_lists_assignments_and_submits(self):
        activity = Activity.objects.create(
            student=self.student, kind="assignment", title="Essay on markets",
            date=date.today(), due_date=date.today())
        response = self.client.get(reverse("my_assignments"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Essay on markets")

        response = self.client.post(
            reverse("assignment_submit", args=[activity.pk]), {"text": "First draft"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            AssignmentSubmission.objects.filter(
                assignment=activity, student=self.student, text="First draft").exists())

    def test_student_can_resubmit_the_same_assignment(self):
        activity = Activity.objects.create(
            student=self.student, kind="assignment", title="Essay on markets",
            date=date.today(), due_date=date.today())
        AssignmentSubmission.objects.create(
            institution=self.student.institution, assignment=activity,
            student=self.student, text="Draft one")
        self.client.post(reverse("assignment_submit", args=[activity.pk]),
                         {"text": "Draft two"})
        self.assertEqual(
            AssignmentSubmission.objects.filter(
                assignment=activity, student=self.student).count(), 1)
        submission = AssignmentSubmission.objects.get(
            assignment=activity, student=self.student)
        self.assertEqual(submission.text, "Draft two")

    def test_assignment_with_other_kind_is_not_listed(self):
        Activity.objects.create(
            student=self.student, kind="club", title="Chess club",
            date=date.today(), due_date=date.today())
        response = self.client.get(reverse("my_assignments"))
        self.assertNotContains(response, "Chess club")


class StudentIdCardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="stu2", password="pw123456", role="student")
        self.programme = Programme.objects.create(
            name="Diploma", code="DIP2", level="diploma",
            tuition_fee=Decimal("100.00"))
        self.student = Student.objects.create(
            user=self.user, student_no="S2", status="enrolled",
            year_or_grade="1", programme=self.programme)
        self.client.force_login(self.user)

    def test_student_downloads_their_own_card_pdf(self):
        StudentCard.objects.create(
            institution=self.student.institution, student=self.student,
            valid_until=date.today() + timedelta(days=365))
        response = self.client.get(reverse("admissions:my_id_card_pdf"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_student_without_a_card_gets_a_message(self):
        response = self.client.get(reverse("admissions:my_id_card_pdf"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("student_portal"), response["Location"])
