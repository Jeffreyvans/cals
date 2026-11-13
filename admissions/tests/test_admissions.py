from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from academics.models import Programme, Student
from accounts.models import User
from admissions import services
from admissions.factories import (
    ApplicantResultFactory,
    ApplicationFactory,
    EntryRequirementFactory,
    OfferFactory,
)
from admissions.models import ApplicantDocument, Application, Offer
from finance.models import Invoice, Payment


class EligibilityTests(TestCase):
    def setUp(self):
        self.application = ApplicationFactory(
            previous_results="Mathematics: B\nEnglish: C\nBiology: D")

    def test_text_results_are_parsed(self):
        rows = services.parse_results_text(
            "Mathematics: B\nEnglish 71\nnot a result line")
        self.assertEqual(rows[0]["subject"], "Mathematics")
        self.assertEqual(rows[0]["grade"], "B")
        self.assertEqual(rows[1]["subject"], "English")
        self.assertEqual(rows[1]["mark"], Decimal("71"))

    def test_no_requirement_leaves_the_result_for_review(self):
        eligible, note = services.check_eligibility(self.application)
        self.assertIsNone(eligible)
        self.assertIn("No entry requirement", note)
        self.application.refresh_from_db()
        self.assertIsNone(self.application.eligible)

    def test_requirement_is_checked_against_grades_and_subjects(self):
        requirement = EntryRequirementFactory(
            programme=self.application.programme,
            minimum_subjects=2,
            minimum_grade="D",
            required_subjects="Mathematics, English")
        eligible, note = services.check_eligibility(self.application, requirement)
        self.assertTrue(eligible)
        self.assertIn("Meets the requirement", note)

    def test_missing_subject_fails_eligibility(self):
        requirement = EntryRequirementFactory(
            programme=self.application.programme,
            minimum_subjects=2,
            minimum_grade="D",
            required_subjects="Mathematics, Physics")
        eligible, note = services.check_eligibility(self.application, requirement)
        self.assertFalse(eligible)
        self.assertIn("Physics", note)

    def test_structured_results_are_preferred_over_text(self):
        ApplicantResultFactory(application=self.application, subject="Mathematics",
                               grade="A")
        ApplicantResultFactory(application=self.application, subject="English",
                               grade="A")
        requirement = EntryRequirementFactory(
            programme=self.application.programme, minimum_subjects=2,
            minimum_grade="B", required_subjects="Mathematics, English")
        eligible, _note = services.check_eligibility(self.application, requirement)
        self.assertTrue(eligible)


class ReviewWorkflowTests(TestCase):
    def setUp(self):
        self.application = ApplicationFactory()

    def test_review_records_the_decision(self):
        services.review_application(
            self.application, Application.Status.WAITLISTED,
            note="Waiting for a place")
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, Application.Status.WAITLISTED)
        self.assertEqual(self.application.decision_note, "Waiting for a place")

    def test_enrolled_application_cannot_be_reviewed_again(self):
        self.application.status = Application.Status.ENROLLED
        self.application.save(update_fields=["status"])
        with self.assertRaises(services.AdmissionError):
            services.review_application(
                self.application, Application.Status.REJECTED)

    def test_offer_requires_a_programme_and_a_future_deadline(self):
        self.application.programme = None
        self.application.save(update_fields=["programme"])
        with self.assertRaises(services.AdmissionError):
            services.make_offer(self.application, timezone.localdate())

    def test_offer_is_numbered_and_appears_on_the_application(self):
        offer = services.make_offer(
            self.application, timezone.localdate() + timedelta(days=7),
            Decimal("50.00"))
        self.assertTrue(offer.offer_no.startswith("OFF"))
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, Application.Status.ACCEPTED)
        self.assertEqual(Offer.objects.count(), 1)


class AcceptanceTests(TestCase):
    def setUp(self):
        self.application = ApplicationFactory()
        self.offer = OfferFactory(
            application=self.application, deposit_amount=Decimal("100.00"))

    def test_acceptance_creates_student_login_invoice_and_payment(self):
        student = services.accept_offer(
            self.offer, accepted_by_name="Parent Name",
            deposit_method=Offer.DepositMethod.RECORDED,
            deposit_reference="RCPT-1")

        self.assertEqual(student.status, Student.Status.ENROLLED)
        self.assertTrue(student.student_no)
        self.assertEqual(student.user.role, User.Role.STUDENT)
        self.assertTrue(student.user.check_password(student.temporary_password))
        self.assertEqual(student.temporary_username, student.user.username)

        invoice = Invoice.objects.get(student=student)
        self.assertEqual(invoice.amount, self.application.programme.tuition_fee)
        payment = Payment.objects.get(invoice=invoice)
        self.assertEqual(payment.amount, Decimal("100.00"))
        self.assertEqual(payment.method, Payment.Method.CASH)

        self.offer.refresh_from_db()
        self.application.refresh_from_db()
        self.assertEqual(self.offer.status, Offer.Status.ACCEPTED)
        self.assertEqual(self.offer.student, student)
        self.assertEqual(self.application.status, Application.Status.ENROLLED)
        self.assertTrue(self.offer.deposit_paid)

    def test_acceptance_is_idempotent(self):
        first = services.accept_offer(
            self.offer, deposit_method=Offer.DepositMethod.RECORDED)
        second = services.accept_offer(self.offer)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Student.objects.count(), 1)
        self.assertEqual(User.objects.filter(role="student").count(), 1)

    def test_expired_offer_is_refused(self):
        self.offer.acceptance_deadline = timezone.localdate() - timedelta(days=1)
        self.offer.save(update_fields=["acceptance_deadline"])
        with self.assertRaises(services.AdmissionError):
            services.accept_offer(self.offer)
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.status, Offer.Status.EXPIRED)
        self.assertFalse(Student.objects.exists())

    def test_deposit_requires_a_method_when_money_is_due(self):
        with self.assertRaises(services.AdmissionError):
            services.accept_offer(self.offer, deposit_method="")

    @patch("admissions.services.send_message")
    def test_failure_rolls_back_the_student(self, send_message):
        send_message.side_effect = RuntimeError("mail server down")
        with self.assertRaises(RuntimeError):
            services.accept_offer(
                self.offer, deposit_method=Offer.DepositMethod.RECORDED)
        self.assertFalse(Student.objects.exists())
        self.assertFalse(User.objects.filter(role="student").exists())
        self.assertFalse(Invoice.objects.exists())
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.status, Offer.Status.PENDING)


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class PublicApplicationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.programme = Programme.objects.create(
            name="Diploma in Business", code="DIB", level="diploma",
            tuition_fee=Decimal("400.00"))
        self.url = reverse("admissions:application_start")

    def payload(self, **overrides):
        data = {
            "first_name": "Tariro",
            "last_name": "Moyo",
            "email": "tariro@example.com",
            "phone": "0771234567",
            "programme": self.programme.pk,
            "results_text": "Mathematics: B\nEnglish: C",
        }
        data.update(overrides)
        return data

    def test_public_form_creates_the_application_and_tracking_page(self):
        response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, 302)
        application = Application.objects.get()
        self.assertTrue(application.reference.startswith("APP"))
        self.assertEqual(application.results.count(), 2)

        page = self.client.get(
            reverse("admissions:track_application",
                    args=[application.tracking_token]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, application.reference)

    def test_honeypot_rejects_the_application(self):
        response = self.client.post(self.url, self.payload(website="spam"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Application.objects.exists())

    def test_rate_limit_stops_the_sixth_application(self):
        for index in range(5):
            self.client.post(self.url, self.payload(
                email=f"applicant{index}@example.com"))
        response = self.client.post(self.url, self.payload(
            email="sixth@example.com"))
        self.assertEqual(response.status_code, 429)
        self.assertEqual(Application.objects.count(), 5)

    def test_documents_are_saved_with_the_application(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        response = self.client.post(self.url, self.payload(
            identity_document=SimpleUploadedFile("id.pdf", b"identity")))
        self.assertEqual(response.status_code, 302)
        document = ApplicantDocument.objects.get()
        self.assertEqual(document.kind, ApplicantDocument.Kind.ID)
