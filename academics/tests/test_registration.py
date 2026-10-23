from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from academics.models import (
    AcademicYear,
    Course,
    CourseOffering,
    CoursePrerequisite,
    Enrollment,
    Programme,
    ProgrammeYear,
    Student,
    Term,
)
from academics.services import (
    RegistrationError,
    drop_enrollment,
    register_student,
    registration_issues,
)
from core.models import AuditEntry

User = get_user_model()


class RegistrationTests(TestCase):
    def setUp(self):
        today = date.today()
        self.year = AcademicYear.objects.create(
            name="2026", start_date=today - timedelta(days=30),
            end_date=today + timedelta(days=300), is_current=True)
        self.term = Term.objects.create(
            academic_year=self.year, name="Term 1",
            start_date=today - timedelta(days=10),
            end_date=today + timedelta(days=100), is_current=True)
        self.programme = Programme.objects.create(
            name="Diploma in IT", code="DIT", level="diploma")
        self.programme_year = ProgrammeYear.objects.create(
            programme=self.programme, academic_year=self.year, number=1,
            capacity=40, credit_limit=15,
            registration_opens=today - timedelta(days=5),
            registration_closes=today + timedelta(days=5))
        self.course = Course.objects.create(
            code="IT101", title="Programming", programme=self.programme, credits=4)
        self.offering = CourseOffering.objects.create(
            course=self.course, term=self.term, capacity=10)
        self.student_user = User.objects.create_user(
            "student", password="pw123456", role="student")
        self.student = Student.objects.create(
            user=self.student_user, programme=self.programme,
            programme_year=self.programme_year, student_no="S100")
        self.registrar = User.objects.create_user(
            "registrar", password="pw123456", role="registrar")
        self.other_user = User.objects.create_user(
            "other", password="pw123456", role="student")
        self.other = Student.objects.create(
            user=self.other_user, programme=self.programme,
            programme_year=self.programme_year, student_no="S101")

    def test_open_window_registers_student(self):
        enrollment = register_student(self.student, self.offering, user=self.student_user)
        self.assertEqual(enrollment.status, Enrollment.Status.REGISTERED)
        self.assertEqual(enrollment.offering, self.offering)
        self.assertEqual(enrollment.term, self.term)
        self.assertTrue(AuditEntry.objects.filter(action="academics.registered").exists())

    def test_closed_term_is_refused(self):
        self.term.is_closed = True
        self.term.save(update_fields=["is_closed"])
        with self.assertRaises(RegistrationError):
            register_student(self.student, self.offering, user=self.student_user)

    def test_registration_window_is_enforced(self):
        self.programme_year.registration_closes = date.today() - timedelta(days=1)
        self.programme_year.save(update_fields=["registration_closes"])
        with self.assertRaises(RegistrationError):
            register_student(self.student, self.offering, user=self.student_user)

    def test_prerequisite_must_be_passed(self):
        prior = Course.objects.create(
            code="IT100", title="Foundations", programme=self.programme, credits=3)
        CoursePrerequisite.objects.create(
            course=self.course, required=prior, min_mark=Decimal("40"))
        with self.assertRaises(RegistrationError):
            register_student(self.student, self.offering, user=self.student_user)

        Enrollment.objects.create(
            student=self.student, course=prior, term=self.term, mark=Decimal("55"))
        enrollment = register_student(self.student, self.offering, user=self.student_user)
        self.assertEqual(enrollment.status, Enrollment.Status.REGISTERED)

    def test_full_offering_puts_student_on_waiting_list(self):
        self.offering.capacity = 1
        self.offering.save(update_fields=["capacity"])
        Enrollment.objects.create(
            student=self.other, course=self.course, term=self.term, offering=self.offering)
        enrollment = register_student(self.student, self.offering, user=self.student_user)
        self.assertEqual(enrollment.status, Enrollment.Status.WAITLISTED)

    def test_drop_frees_a_place_for_the_waiting_list(self):
        self.offering.capacity = 1
        self.offering.save(update_fields=["capacity"])
        first = Enrollment.objects.create(
            student=self.other, course=self.course, term=self.term, offering=self.offering)
        waiting = register_student(self.student, self.offering, user=self.student_user)
        self.assertEqual(waiting.status, Enrollment.Status.WAITLISTED)

        drop_enrollment(first, user=self.registrar, reason="Timetable clash")
        waiting.refresh_from_db()
        self.assertEqual(waiting.status, Enrollment.Status.REGISTERED)

    def test_credit_limit_is_enforced(self):
        second = Course.objects.create(
            code="IT102", title="Networking", programme=self.programme, credits=12)
        self.offering.credits = 4
        self.offering.save(update_fields=["credits"])
        Enrollment.objects.create(
            student=self.student, course=self.course, term=self.term,
            offering=self.offering)
        offering = CourseOffering.objects.create(course=second, term=self.term)
        issues = registration_issues(self.student, offering)
        self.assertTrue(any("limit is 15" in issue for issue in issues))

    def test_duplicate_registration_is_refused(self):
        register_student(self.student, self.offering, user=self.student_user)
        with self.assertRaises(RegistrationError):
            register_student(self.student, self.offering, user=self.student_user)

    def test_override_needs_a_reason_and_an_authorised_user(self):
        self.term.is_closed = True
        self.term.save(update_fields=["is_closed"])
        with self.assertRaises(RegistrationError):
            register_student(
                self.student, self.offering, user=self.registrar, override=True)
        with self.assertRaises(RegistrationError):
            register_student(
                self.student, self.offering, user=self.student_user,
                override=True, reason="Please")
        enrollment = register_student(
            self.student, self.offering, user=self.registrar,
            override=True, reason="Late transfer approved")
        self.assertEqual(enrollment.status, Enrollment.Status.REGISTERED)
        entry = AuditEntry.objects.get(action="academics.registration_override")
        self.assertEqual(entry.after["reason"], "Late transfer approved")

    def test_student_registers_from_their_own_screen(self):
        self.client.force_login(self.student_user)
        response = self.client.post(reverse("my_registration"), {
            "offering": self.offering.pk, "term": self.term.pk,
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Enrollment.objects.filter(
            student=self.student, offering=self.offering,
            status=Enrollment.Status.REGISTERED).exists())

    def test_student_cannot_open_another_students_registration(self):
        self.client.force_login(self.student_user)
        response = self.client.get(reverse("course_registration", args=[self.other.pk]))
        self.assertEqual(response.status_code, 403)

    def test_registrar_can_register_a_student_and_see_the_screen(self):
        self.client.force_login(self.registrar)
        url = reverse("course_registration", args=[self.student.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {
            "offering": self.offering.pk, "term": self.term.pk,
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Enrollment.objects.filter(
            student=self.student, offering=self.offering).exists())
