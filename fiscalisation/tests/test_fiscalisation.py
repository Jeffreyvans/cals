"""Fiscalisation against ZIMRA's FDMS.

The mock gateway lets the whole flow run without credentials. These tests
prove that a VAT-registered institution submits invoices, receipts, credit
notes and debit notes, that the reply (verification code and QR data) is
stored, that a fiscal day opens and closes, that failures are recorded and
retried, and that a non-registered institution is never submitted.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from academics.models import Programme, Student
from core.models import Institution
from core.modules import set_module
from core.tenancy import use_institution
from finance.models import DebitNote, DocumentType, Invoice, Payment, Waiver

from .. import services
from ..models import FiscalDay, FiscalDevice, FiscalSubmission

User = get_user_model()
TODAY = timezone.localdate()


def make_student(institution, tag):
    with use_institution(institution):
        programme = Programme.objects.create(
            name=f"Programme {tag}", code=f"P{tag}", level="diploma")
        user = User.objects.create_user(
            f"u{tag}", password="pw123456", role="student",
            institution=institution)
        return Student.objects.create(
            user=user, programme=programme, student_no=f"S{tag}",
            institution=institution)


class FiscalisationBase(TestCase):
    def setUp(self):
        self.bursar = User.objects.create_user(
            "fis_bur", password="pw123456", role="bursar")
        self.institution = Institution.objects.create(
            name="Fiscal College", slug="fiscal-college", type="college",
            vat_status=Institution.VatStatus.REGISTERED,
            vat_number="1000999", bp_number="2000999")
        set_module(self.institution, "finance", True)
        set_module(self.institution, "fiscalisation", True)
        self.student = make_student(self.institution, "FIS")

    def create_invoice(self, amount="100.00", description="Tuition"):
        with use_institution(self.institution):
            return Invoice.objects.create(
                student=self.student, description=description,
                amount=Decimal(amount), due_date=TODAY)

    def submission(self, document_type, instance):
        return FiscalSubmission.all_objects.get(
            institution=self.institution, document_type=document_type,
            document_id=instance.pk)


class SubmissionTests(FiscalisationBase):
    def test_invoice_is_submitted_and_verified(self):
        invoice = self.create_invoice()
        submission = self.submission(DocumentType.INVOICE, invoice)
        self.assertEqual(submission.status, FiscalSubmission.Status.VERIFIED)
        self.assertEqual(submission.document_number, invoice.number)
        self.assertTrue(submission.verification_code)
        self.assertTrue(
            submission.qr_data.startswith("https://fdms.zimra.co.zw/verify/"))
        self.assertEqual(submission.response["gateway"], "mock")
        self.assertIsNotNone(submission.fiscal_day)
        self.assertEqual(submission.fiscal_day.fiscal_day_no, 1)
        self.assertEqual(submission.attempts, 1)

    def test_receipts_and_notes_are_submitted_too(self):
        invoice = self.create_invoice()
        with use_institution(self.institution):
            payment = Payment.objects.create(
                invoice=invoice, amount=Decimal("40.00"))
            waiver = Waiver.objects.create(
                invoice=invoice, amount=Decimal("10.00"), reason="Bursary")
            note = DebitNote.objects.create(
                invoice=invoice, amount=Decimal("5.00"), reason="Late fee")
        for document_type, instance in (
                (DocumentType.RECEIPT, payment),
                (DocumentType.CREDIT_NOTE, waiver),
                (DocumentType.DEBIT_NOTE, note)):
            submission = self.submission(document_type, instance)
            self.assertEqual(submission.status,
                             FiscalSubmission.Status.VERIFIED,
                             document_type)
            self.assertTrue(submission.verification_code, document_type)

    def test_each_document_gets_its_own_verification_code(self):
        first = self.create_invoice(description="First")
        second = self.create_invoice(description="Second")
        codes = {
            self.submission(DocumentType.INVOICE, first).verification_code,
            self.submission(DocumentType.INVOICE, second).verification_code,
        }
        self.assertEqual(len(codes), 2)

    def test_device_tracks_the_last_fiscal_day(self):
        self.create_invoice()
        device = FiscalDevice.all_objects.get(institution=self.institution)
        self.assertEqual(device.last_fiscal_day_no, 1)

    def test_receipt_shows_the_verification_code_and_qr(self):
        invoice = self.create_invoice()
        with use_institution(self.institution):
            payment = Payment.objects.create(
                invoice=invoice, amount=Decimal("40.00"))
        submission = self.submission(DocumentType.RECEIPT, payment)
        self.bursar.institution = self.institution
        self.bursar.save(update_fields=["institution"])
        self.client.force_login(self.bursar)
        response = self.client.get(reverse("receipt", args=[payment.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, submission.verification_code)
        self.assertContains(
            response, reverse("fiscalisation:qr", args=[submission.pk]))

    def test_qr_endpoint_returns_a_png(self):
        invoice = self.create_invoice()
        submission = self.submission(DocumentType.INVOICE, invoice)
        response = self.client.get(
            reverse("fiscalisation:qr", args=[submission.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")


class FiscalDayTests(FiscalisationBase):
    def test_a_day_opens_at_number_one_and_can_be_closed(self):
        day = services.open_fiscal_day(self.institution, user=self.bursar)
        self.assertEqual(day.fiscal_day_no, 1)
        self.assertEqual(day.status, FiscalDay.Status.OPEN)
        self.assertEqual(
            services.current_fiscal_day(self.institution).pk, day.pk)

        services.close_fiscal_day(self.institution, user=self.bursar)
        day.refresh_from_db()
        self.assertEqual(day.status, FiscalDay.Status.CLOSED)

    def test_opening_a_closed_day_is_refused(self):
        services.open_fiscal_day(self.institution)
        services.close_fiscal_day(self.institution)
        with self.assertRaises(ValidationError):
            services.open_fiscal_day(self.institution)

    def test_closing_a_missing_day_is_refused(self):
        with self.assertRaises(ValidationError):
            services.close_fiscal_day(self.institution)


class FailureAndRetryTests(FiscalisationBase):
    @override_settings(
        FISCALISATION_GATEWAY="zimra", ZIMRA_FDMS_URL="", ZIMRA_API_KEY="")
    def test_a_gateway_failure_does_not_break_the_invoice(self):
        invoice = self.create_invoice()
        self.assertTrue(Invoice.all_objects.filter(pk=invoice.pk).exists())
        submission = self.submission(DocumentType.INVOICE, invoice)
        self.assertEqual(submission.status, FiscalSubmission.Status.FAILED)
        self.assertIn("not configured", submission.last_error)
        self.assertFalse(submission.verification_code)

    def test_a_failed_submission_is_retried(self):
        invoice = self.create_invoice()
        submission = self.submission(DocumentType.INVOICE, invoice)
        submission.status = FiscalSubmission.Status.FAILED
        submission.last_error = "previous failure"
        submission.verification_code = ""
        submission.save()
        retried = services.retry_failed_submissions(self.institution)
        self.assertEqual(retried, 1)
        submission.refresh_from_db()
        self.assertEqual(submission.status, FiscalSubmission.Status.VERIFIED)
        self.assertTrue(submission.verification_code)


class SkippedInstitutionTests(TestCase):
    def test_a_non_registered_institution_is_never_submitted(self):
        institution = Institution.objects.create(
            name="Exempt College", slug="exempt-fiscal", type="college")
        set_module(institution, "finance", True)
        set_module(institution, "fiscalisation", True)
        student = make_student(institution, "EXF")
        with use_institution(institution):
            Invoice.objects.create(
                student=student, description="Tuition",
                amount=Decimal("100.00"), due_date=TODAY)
        self.assertEqual(FiscalSubmission.all_objects.count(), 0)

    def test_the_module_being_off_skips_submission(self):
        institution = Institution.objects.create(
            name="Off College", slug="off-fiscal", type="college",
            vat_status=Institution.VatStatus.REGISTERED)
        set_module(institution, "finance", True)
        set_module(institution, "fiscalisation", False)
        student = make_student(institution, "OFF")
        with use_institution(institution):
            Invoice.objects.create(
                student=student, description="Tuition",
                amount=Decimal("100.00"), due_date=TODAY)
        self.assertEqual(FiscalSubmission.all_objects.count(), 0)

    @override_settings(FISCALISATION_ENABLED=False)
    def test_the_global_switch_skips_submission(self):
        institution = Institution.objects.create(
            name="Disabled College", slug="disabled-fiscal", type="college",
            vat_status=Institution.VatStatus.REGISTERED)
        set_module(institution, "finance", True)
        set_module(institution, "fiscalisation", True)
        student = make_student(institution, "DIS")
        with use_institution(institution):
            Invoice.objects.create(
                student=student, description="Tuition",
                amount=Decimal("100.00"), due_date=TODAY)
        self.assertEqual(FiscalSubmission.all_objects.count(), 0)


class DocumentInstanceTests(FiscalisationBase):
    def test_a_submission_resolves_back_to_its_document(self):
        invoice = self.create_invoice()
        submission = self.submission(DocumentType.INVOICE, invoice)
        self.assertEqual(
            services.document_instance(submission).pk, invoice.pk)

    def test_a_missing_document_returns_none(self):
        invoice = self.create_invoice()
        submission = self.submission(DocumentType.INVOICE, invoice)
        submission.document_id = 999999
        self.assertIsNone(services.document_instance(submission))
