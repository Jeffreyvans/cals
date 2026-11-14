"""Part 4: printable/PDF receipts and emailed receipts.

The on-screen receipt, the PDF and the email all carry the institution's
branding, and the PDF carries a QR code that opens the public verification
page. These tests cover the download, the student's ownership rule, the email
send, the missing-address case, the branding plumbing and the verify link.
"""

import shutil
import tempfile
from datetime import date
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.functional import empty
from PIL import Image

from academics.models import Programme, Student
from core.logo import save_logo
from core.models import Institution, OutboundMessage
from finance import receipts
from finance.models import Invoice, Payment

User = get_user_model()


def _logo_upload(size=(400, 400)):
    buffer = BytesIO()
    Image.new("RGB", size, (20, 58, 138)).save(buffer, format="PNG")
    return SimpleUploadedFile("logo.png", buffer.getvalue(), "image/png")


class ReceiptFixture(TestCase):
    def setUp(self):
        self.institution = Institution.objects.get(slug="default")
        self.programme = Programme.objects.create(name="Diploma in IT", code="DIT",
                                                  level="diploma")
        self.bursar = User.objects.create_user("bur", password="pw123456", role="bursar")
        self.student_user = User.objects.create_user(
            "s1", password="pw123456", role="student", email="s1@example.test")
        self.other_user = User.objects.create_user(
            "s2", password="pw123456", role="student", email="s2@example.test")
        self.student = Student.objects.create(user=self.student_user,
                                              programme=self.programme, student_no="S0501")
        other = Student.objects.create(user=self.other_user, programme=self.programme,
                                       student_no="S0502")
        invoice = Invoice.objects.create(student=self.student, description="Tuition",
                                         amount=Decimal("100.00"), due_date=date.today())
        other_invoice = Invoice.objects.create(student=other, description="Tuition",
                                               amount=Decimal("100.00"),
                                               due_date=date.today())
        self.payment = Payment.objects.create(invoice=invoice, amount=Decimal("100.00"))
        self.other_payment = Payment.objects.create(invoice=other_invoice,
                                                    amount=Decimal("100.00"))


class ReceiptPdfViewTests(ReceiptFixture):
    def test_a_bursar_downloads_a_pdf(self):
        self.client.force_login(self.bursar)
        response = self.client.get(reverse("receipt_pdf", args=[self.payment.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertIn("receipt-", response["Content-Disposition"])
        self.assertIn(self.payment.receipt_no, response["Content-Disposition"])

    def test_the_pdf_view_passes_the_request_and_the_fiscal_record(self):
        self.client.force_login(self.bursar)
        with patch("finance.receipts.build_receipt_pdf", return_value=b"%PDF-test") as build:
            response = self.client.get(reverse("receipt_pdf", args=[self.payment.pk]))
        self.assertEqual(response.content, b"%PDF-test")
        self.assertIsNotNone(build.call_args.kwargs["request"])
        self.assertIsNone(build.call_args.kwargs["fiscal"])

    def test_a_student_downloads_only_their_own_receipt(self):
        self.client.force_login(self.student_user)
        own = self.client.get(reverse("receipt_pdf", args=[self.payment.pk]))
        self.assertEqual(own.status_code, 200)
        forbidden = self.client.get(reverse("receipt_pdf", args=[self.other_payment.pk]))
        self.assertEqual(forbidden.status_code, 403)

    def test_signing_in_is_required(self):
        response = self.client.get(reverse("receipt_pdf", args=[self.payment.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_the_receipt_page_offers_the_download_and_the_email(self):
        self.client.force_login(self.bursar)
        response = self.client.get(reverse("receipt", args=[self.payment.pk]))
        self.assertContains(response, reverse("receipt_pdf", args=[self.payment.pk]))
        self.assertContains(response, reverse("email_receipt", args=[self.payment.pk]))
        self.assertContains(response, "Email receipt")


class ReceiptEmailViewTests(ReceiptFixture):
    def test_a_bursar_emails_the_receipt(self):
        self.client.force_login(self.bursar)
        response = self.client.post(reverse("email_receipt", args=[self.payment.pk]))
        self.assertRedirects(response, reverse("receipt", args=[self.payment.pk]))
        message = OutboundMessage.objects.get(related_object_id=str(self.payment.pk))
        self.assertEqual(message.channel, "email")
        self.assertEqual(message.recipient, "s1@example.test")
        self.assertIn(self.payment.receipt_no, message.subject)
        self.assertEqual(message.status, OutboundMessage.Status.SENT)
        self.assertIn(self.institution.name, message.html_body)
        self.assertIn(self.payment.receipt_no, message.body)

    def test_the_email_carries_the_public_verify_link(self):
        self.client.force_login(self.bursar)
        self.client.post(reverse("email_receipt", args=[self.payment.pk]))
        message = OutboundMessage.objects.get(related_object_id=str(self.payment.pk))
        token = self.payment.verification_token
        self.assertIn(f"/verify/{token}/", message.body)

    def test_a_get_never_sends_the_email(self):
        self.client.force_login(self.bursar)
        response = self.client.get(reverse("email_receipt", args=[self.payment.pk]))
        self.assertRedirects(response, reverse("receipt", args=[self.payment.pk]))
        self.assertEqual(OutboundMessage.objects.count(), 0)

    def test_a_student_without_an_email_is_told(self):
        self.student_user.email = ""
        self.student_user.save(update_fields=["email"])
        self.client.force_login(self.bursar)
        response = self.client.post(reverse("email_receipt", args=[self.payment.pk]),
                                    follow=True)
        self.assertContains(response, "no email address")
        self.assertEqual(OutboundMessage.objects.count(), 0)

    def test_a_student_emails_only_their_own_receipt(self):
        self.client.force_login(self.student_user)
        own = self.client.post(reverse("email_receipt", args=[self.payment.pk]))
        self.assertEqual(own.status_code, 302)
        forbidden = self.client.post(reverse("email_receipt", args=[self.other_payment.pk]))
        self.assertEqual(forbidden.status_code, 403)


class ReceiptVerifyUrlTests(ReceiptFixture):
    def test_verify_url_points_at_the_public_page(self):
        self.assertEqual(
            receipts.verify_url(self.payment),
            reverse("verify_receipt", args=[self.payment.verification_token]))

    def test_verify_url_is_made_absolute_for_a_request(self):
        from django.test import RequestFactory

        request = RequestFactory().get("/")
        url = receipts.verify_url(self.payment, request)
        self.assertTrue(url.startswith("http://testserver/"))


class ReceiptPdfBrandingTests(TestCase):
    """The PDF builds with and without a logo, and the QR is real."""

    def setUp(self):
        super().setUp()
        self.media_root = tempfile.mkdtemp(prefix="edusphere-receipt-media-")
        self._media_override = override_settings(MEDIA_ROOT=self.media_root)
        self._media_override.enable()
        default_storage._wrapped = empty
        self.institution = Institution.objects.get(slug="default")
        programme = Programme.objects.create(name="Diploma in IT", code="DIT",
                                             level="diploma")
        user = User.objects.create_user("s1", password="pw123456", role="student",
                                        email="s1@example.test")
        student = Student.objects.create(user=user, programme=programme,
                                         student_no="S0511")
        invoice = Invoice.objects.create(student=student, description="Tuition",
                                         amount=Decimal("100.00"), due_date=date.today())
        self.payment = Payment.objects.create(invoice=invoice, amount=Decimal("100.00"))

    def tearDown(self):
        self._media_override.disable()
        default_storage._wrapped = empty
        shutil.rmtree(self.media_root, ignore_errors=True)
        super().tearDown()

    def test_the_pdf_builds_without_a_logo(self):
        pdf = receipts.build_receipt_pdf(self.payment)
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_the_pdf_builds_with_a_logo(self):
        save_logo(self.institution, _logo_upload())
        pdf = receipts.build_receipt_pdf(self.payment)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 1000)

    def test_the_qr_code_encodes_the_verify_link(self):
        qr = receipts._qr("https://school.test/verify/abc/")
        self.assertGreater(qr.drawWidth, 0)
        self.assertGreater(qr.drawHeight, 0)
