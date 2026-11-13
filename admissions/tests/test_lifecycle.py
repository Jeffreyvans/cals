from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from academics.factories import ProgrammeFactory, StudentFactory
from academics.models import Student
from admissions import services
from admissions.factories import (
    CertificateFactory,
    GraduationClearanceFactory,
    StudentCardFactory,
)
from admissions.models import (
    AlumniRecord,
    GraduationClearance,
    LifecycleState,
    LifecycleTransition,
    StudentCard,
    TransferRecord,
)
from finance.models import Invoice


class TransitionTests(TestCase):
    def setUp(self):
        self.student = StudentFactory(status=Student.Status.ENROLLED)

    def test_reason_is_required(self):
        with self.assertRaises(services.AdmissionError):
            services.transition_student(self.student, LifecycleState.DEFERRED, "")

    def test_defer_and_return_are_recorded(self):
        services.transition_student(
            self.student, LifecycleState.DEFERRED, "Medical leave")
        self.student.refresh_from_db()
        self.assertEqual(self.student.status, Student.Status.DEFERRED)
        services.transition_student(
            self.student, LifecycleState.ENROLLED, "Returned to study")
        self.student.refresh_from_db()
        self.assertEqual(self.student.status, Student.Status.ENROLLED)
        self.assertEqual(LifecycleTransition.objects.count(), 2)

    def test_graduation_must_use_the_graduation_service(self):
        with self.assertRaises(services.AdmissionError):
            services.transition_student(
                self.student, LifecycleState.GRADUATED, "Finished")

    def test_an_invalid_transition_is_refused(self):
        services.transition_student(
            self.student, LifecycleState.WITHDRAWN, "Withdrew")
        with self.assertRaises(services.AdmissionError):
            services.transition_student(
                self.student, LifecycleState.DEFERRED, "Cannot defer a withdrawn student")


class TransferTests(TestCase):
    def setUp(self):
        self.student = StudentFactory(status=Student.Status.ENROLLED)

    def test_transfer_out_records_the_move_and_changes_status(self):
        record = services.transfer_out(
            self.student, "Another College", reference="REF-1",
            reason="Family relocated")
        self.student.refresh_from_db()
        self.assertEqual(record.direction, TransferRecord.Direction.OUT)
        self.assertEqual(self.student.status, Student.Status.TRANSFERRED_OUT)
        self.assertEqual(TransferRecord.objects.count(), 1)

    def test_transfer_in_returns_the_student_to_enrolled(self):
        self.student.status = Student.Status.TRANSFERRED_OUT
        self.student.save(update_fields=["status"])
        record = services.transfer_in(
            self.student, "Previous College", reason="Joined the programme")
        self.student.refresh_from_db()
        self.assertEqual(record.direction, TransferRecord.Direction.IN)
        self.assertEqual(self.student.status, Student.Status.ENROLLED)

    def test_transfer_needs_an_institution_and_a_reason(self):
        with self.assertRaises(services.AdmissionError):
            services.transfer_out(self.student, "", reason="Moved")
        with self.assertRaises(services.AdmissionError):
            services.transfer_out(self.student, "Another College", reason="")


class GraduationTests(TestCase):
    def setUp(self):
        self.student = StudentFactory(status=Student.Status.ENROLLED)

    def test_clearance_is_created_empty(self):
        clearance = services.clearance_for(self.student)
        self.assertFalse(clearance.is_clear)
        self.assertEqual(len(clearance.outstanding), 4)

    def test_graduation_is_blocked_until_every_item_is_clear(self):
        services.update_clearance(self.student, {"exams_passed": True})
        with self.assertRaises(services.AdmissionError):
            services.graduate_student(self.student)
        self.student.refresh_from_db()
        self.assertEqual(self.student.status, Student.Status.ENROLLED)

    def test_clearance_and_graduation_create_the_alumni_record(self):
        services.update_clearance(self.student, {
            "fees_cleared": True,
            "library_books_returned": True,
            "hostel_keys_returned": True,
            "exams_passed": True,
        })
        services.graduate_student(self.student)
        self.student.refresh_from_db()
        self.assertEqual(self.student.status, Student.Status.GRADUATED)
        self.assertTrue(AlumniRecord.objects.filter(student=self.student).exists())
        clearance = GraduationClearance.objects.get(student=self.student)
        self.assertIsNotNone(clearance.graduated_at)

    def test_fee_balance_can_refresh_the_clearance(self):
        Invoice.objects.create(
            student=self.student, description="Tuition", amount=100,
            due_date=timezone.localdate())
        clearance = services.update_clearance(
            self.student, {"refresh_fees": True})
        self.assertFalse(clearance.fees_cleared)

    def test_a_graduated_student_can_move_to_alumni(self):
        GraduationClearanceFactory(student=self.student)
        self.student.status = Student.Status.GRADUATED
        self.student.save(update_fields=["status"])
        services.transition_student(
            self.student, LifecycleState.ALUMNI, "Joined the alumni association")
        self.student.refresh_from_db()
        self.assertEqual(self.student.status, Student.Status.ALUMNI)


class CertificateAndCardTests(TestCase):
    def setUp(self):
        self.student = StudentFactory(
            status=Student.Status.GRADUATED,
            programme=ProgrammeFactory(name="Diploma in IT", code="DIT"))

    def test_only_a_graduated_student_receives_a_certificate(self):
        active = StudentFactory()
        with self.assertRaises(services.AdmissionError):
            services.issue_certificate(active, "Diploma")

    def test_certificate_has_a_serial_number(self):
        certificate = services.issue_certificate(
            self.student, "Diploma in IT", title="Certificate of Award")
        self.assertTrue(certificate.serial_no.startswith("CERT"))
        self.assertIsNotNone(certificate.token)

    def test_card_can_be_issued_and_reissued(self):
        first = services.issue_id_card(
            self.student, timezone.localdate() + timedelta(days=365))
        second = services.issue_id_card(
            self.student, timezone.localdate() + timedelta(days=730))
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(StudentCard.objects.count(), 1)
        self.assertTrue(second.is_valid)

    def test_expired_card_is_not_valid(self):
        card = StudentCardFactory(
            student=self.student,
            valid_until=timezone.localdate() - timedelta(days=1))
        self.assertFalse(card.is_valid)

    def test_certificate_verification_page_is_public(self):
        certificate = CertificateFactory(student=self.student)
        response = self.client.get(
            f"/admissions/verify/certificate/{certificate.token}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, certificate.serial_no)

    def test_card_verification_page_is_public(self):
        card = StudentCardFactory(student=self.student)
        response = self.client.get(f"/admissions/verify/card/{card.token}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, card.card_no)
