"""Part 4: one branded masthead for every document.

The same logo, name, address, contacts and VAT/BP numbers appear on receipts,
statements, invoices, notes, report cards, transcripts, identity cards and
emails. A fiscalised document keeps the masthead it was issued with.
"""

import shutil
import tempfile
from datetime import date
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image
from reportlab.graphics.shapes import Drawing
from reportlab.platypus import Table

from academics.models import Programme, Student
from core.document_header import (
    build_header,
    contact_lines,
    header_context,
    header_mark,
    initials_flowable,
    institution_details,
    snapshot_header,
)
from core.logo import save_logo
from core.models import Institution
from finance.models import DocumentType, Invoice, Payment
from fiscalisation.models import FiscalSubmission

User = get_user_model()


def _logo_upload(size=(400, 400)):
    buffer = BytesIO()
    Image.new("RGB", size, (20, 58, 138)).save(buffer, format="PNG")
    return SimpleUploadedFile("logo.png", buffer.getvalue(), "image/png")


class DocumentHeaderTestCase(TestCase):
    def setUp(self):
        self.institution = Institution.objects.get(slug="default")
        self.institution.name = "Mavambo College"
        self.institution.address = "12 Samora Machel Avenue, Harare"
        self.institution.contact_phone = "+263 242 700 100"
        self.institution.contact_email = "office@mavambo.test"
        self.institution.website = "https://mavambo.test"
        self.institution.vat_number = "VAT-100200"
        self.institution.bp_number = "BP-300400"
        self.institution.save()
        self.programme = Programme.objects.create(
            name="Diploma in IT", code="DIT", level="diploma")
        self.student_user = User.objects.create_user(
            "s1", password="pw123456", role="student",
            email="s1@example.test")
        self.student = Student.objects.create(
            user=self.student_user, programme=self.programme,
            student_no="S0501")
        self.invoice = Invoice.objects.create(
            student=self.student, description="Tuition",
            amount=Decimal("100.00"), due_date=date.today())
        self.payment = Payment.objects.create(
            invoice=self.invoice, amount=Decimal("100.00"))


class DetailsTests(DocumentHeaderTestCase):
    def test_details_include_every_field_that_is_set(self):
        details = institution_details(self.institution)
        self.assertEqual(details["name"], "Mavambo College")
        self.assertEqual(details["address"], "12 Samora Machel Avenue, Harare")
        self.assertEqual(details["phone"], "+263 242 700 100")
        self.assertEqual(details["email"], "office@mavambo.test")
        self.assertEqual(details["website"], "https://mavambo.test")
        self.assertEqual(details["vat_number"], "VAT-100200")
        self.assertEqual(details["bp_number"], "BP-300400")

    def test_contact_lines_have_address_then_contacts_then_tax(self):
        lines = contact_lines(institution_details(self.institution))
        self.assertEqual(lines[0], "12 Samora Machel Avenue, Harare")
        self.assertIn("+263 242 700 100", lines[1])
        self.assertIn("office@mavambo.test", lines[1])
        self.assertEqual(lines[2], "VAT VAT-100200 · BP BP-300400")

    def test_a_school_without_vat_has_no_empty_tax_line(self):
        self.institution.vat_number = ""
        self.institution.bp_number = ""
        lines = contact_lines(institution_details(self.institution))
        self.assertFalse(any(line.startswith("VAT") for line in lines))

    def test_missing_details_never_raise(self):
        details = institution_details(None)
        self.assertEqual(details["name"], "")
        self.assertEqual(details["initials"], "?")


class MarkTests(DocumentHeaderTestCase):
    def test_without_a_logo_the_initials_circle_is_used(self):
        mark = header_mark(self.institution, size=40)
        self.assertIsInstance(mark, Drawing)

    def test_initials_flowable_draws_a_circle(self):
        drawing = initials_flowable("MC", 40)
        self.assertIsInstance(drawing, Drawing)

    def test_a_missing_logo_file_falls_back_instead_of_breaking(self):
        self.institution.logo = "institutions/missing/logo.png"
        self.institution.settings = {
            **(self.institution.settings or {}),
            "logo": {"512": "institutions/missing/logo.png"},
        }
        self.institution.save()
        self.assertIsInstance(header_mark(self.institution, size=40), Drawing)

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_a_real_logo_is_used_and_a_snapshot_copies_it(self):
        try:
            save_logo(self.institution, _logo_upload())
            self.institution.refresh_from_db()
            self.assertNotIsInstance(header_mark(self.institution, size=40), Drawing)
            snapshot = snapshot_header(self.institution)
            self.assertEqual(snapshot["details"]["name"], "Mavambo College")
            self.assertTrue(snapshot["logo"])
            self.assertNotEqual(snapshot["logo"], self.institution.logo.name)
            from django.core.files.storage import default_storage
            self.assertTrue(default_storage.exists(snapshot["logo"]))
        finally:
            from django.conf import settings
            shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)

    def test_build_header_returns_a_three_column_table(self):
        header = build_header(self.institution, "Payment receipt",
                              subtitle="Copy")
        self.assertIsInstance(header, Table)
        self.assertEqual(len(header._colWidths), 3)


class SnapshotTests(DocumentHeaderTestCase):
    def test_a_stored_layout_wins_over_the_live_institution(self):
        layout = {
            "details": {
                "name": "Old Name College", "address": "Old address",
                "phone": "", "email": "", "website": "",
                "vat_number": "", "bp_number": "", "initials": "ON",
            },
            "logo": "",
        }
        context = header_context(self.institution, layout=layout)
        self.assertEqual(context["details"]["name"], "Old Name College")
        self.assertEqual(context["lines"][0], "Old address")

    def test_the_receipt_screen_uses_a_fiscalised_snapshot(self):
        FiscalSubmission.objects.create(
            institution=self.institution,
            document_type=DocumentType.RECEIPT,
            document_id=self.payment.pk,
            document_number=self.payment.receipt_no,
            layout={
                "details": {
                    "name": "Issued Name College", "address": "Issued address",
                    "phone": "", "email": "", "website": "",
                    "vat_number": "", "bp_number": "", "initials": "IN",
                },
                "logo": "",
            })
        self.institution.name = "Changed Name College"
        self.institution.save(update_fields=["name"])
        self.client.force_login(self.student_user)
        response = self.client.get(reverse("receipt", args=[self.payment.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Issued Name College")
        self.assertContains(response, "Issued address")


class EmailHeaderTests(DocumentHeaderTestCase):
    def test_a_receipt_email_carries_the_details(self):
        self.client.force_login(self.student_user)
        self.client.post(reverse("email_receipt", args=[self.payment.pk]))
        from core.models import OutboundMessage

        message = OutboundMessage.objects.get(
            related_object_id=str(self.payment.pk))
        self.assertIn("Mavambo College", message.html_body)
        self.assertIn("12 Samora Machel Avenue, Harare", message.html_body)
        self.assertIn("VAT VAT-100200", message.html_body)
