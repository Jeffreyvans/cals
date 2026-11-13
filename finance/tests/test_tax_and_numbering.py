"""Tax and gap-free document numbering rules.

These cover the ZIMRA-facing rules added to the finance module: dated VAT
rates, VAT that follows the rate in force, exempt institutions that never
charge VAT, gap-free document numbers (including under concurrent allocation),
and the immutability of issued documents.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from academics.models import Programme, Student
from core.models import Institution
from core.tenancy import use_institution
from finance import services
from finance.models import (
    DebitNote,
    DocumentSequence,
    DocumentType,
    Expense,
    FeeItem,
    FeeStructure,
    Invoice,
    Payment,
    TaxCategory,
    TaxRate,
    Waiver,
)

User = get_user_model()
TODAY = timezone.localdate()
YEAR = TODAY.year


def make_student(institution, tag):
    """Create a student inside an institution and return it."""
    with use_institution(institution):
        programme = Programme.objects.create(
            name=f"Programme {tag}", code=f"P{tag}", level="diploma")
        user = User.objects.create_user(
            f"u{tag}", password="pw123456", role="student",
            institution=institution)
        return Student.objects.create(
            user=user, programme=programme, student_no=f"S{tag}",
            institution=institution)


class DocumentNumberingTests(TestCase):
    """Numbers run 1, 2, 3 ... with no gaps, per institution and year."""

    def setUp(self):
        self.institution = Institution.objects.create(
            name="Numbering College", slug="numbering-college", type="college")
        self.student = make_student(self.institution, "NUM")

    def test_invoice_numbers_are_gap_free_and_sequential(self):
        numbers = []
        with use_institution(self.institution):
            for i in range(5):
                invoice = Invoice.objects.create(
                    student=self.student, description=f"Fee {i}",
                    amount=Decimal("100.00"), due_date=TODAY)
                numbers.append(invoice.number)
        self.assertEqual(
            numbers, [f"INV-{YEAR}-{n:06d}" for n in range(1, 6)])
        sequence = DocumentSequence.all_objects.get(
            institution=self.institution, document_type=DocumentType.INVOICE,
            financial_year=str(YEAR))
        self.assertEqual(sequence.last_number, 5)

    def test_each_document_type_has_its_own_sequence(self):
        with use_institution(self.institution):
            invoice = Invoice.objects.create(
                student=self.student, description="Tuition",
                amount=Decimal("100.00"), due_date=TODAY)
            payment = Payment.objects.create(
                invoice=invoice, amount=Decimal("40.00"))
            waiver = Waiver.objects.create(
                invoice=invoice, amount=Decimal("10.00"), reason="Bursary")
            note = DebitNote.objects.create(
                invoice=invoice, amount=Decimal("5.00"), reason="Late fee")
        self.assertEqual(payment.receipt_no, f"RC-{YEAR}-000001")
        self.assertEqual(waiver.note_no, f"CN-{YEAR}-000001")
        self.assertEqual(note.note_no, f"DN-{YEAR}-000001")

    def test_numbers_are_allocated_per_institution(self):
        other = Institution.objects.create(
            name="Second College", slug="second-numbering", type="college")
        other_student = make_student(other, "OTH")
        with use_institution(self.institution):
            first = Invoice.objects.create(
                student=self.student, description="Tuition",
                amount=Decimal("100.00"), due_date=TODAY)
        with use_institution(other):
            second = Invoice.objects.create(
                student=other_student, description="Tuition",
                amount=Decimal("100.00"), due_date=TODAY)
        self.assertEqual(first.number, f"INV-{YEAR}-000001")
        self.assertEqual(second.number, f"INV-{YEAR}-000001")

    def test_explicit_number_is_respected(self):
        with use_institution(self.institution):
            invoice = Invoice.objects.create(
                student=self.student, description="Manual",
                amount=Decimal("10.00"), due_date=TODAY,
                number="INV-CUSTOM-1")
        self.assertEqual(invoice.number, "INV-CUSTOM-1")


class ConcurrentNumberingTests(TransactionTestCase):
    """Simultaneous requests never share a number and leave no gaps."""

    def setUp(self):
        self.institution = Institution.objects.create(
            name="Concurrent College", slug="concurrent-college",
            type="college")

    def test_concurrent_allocations_are_unique_and_gap_free(self):
        count = 10
        errors = []

        def worker(_index):
            try:
                return services.next_document_number(
                    self.institution, DocumentType.INVOICE)
            except Exception as exc:  # pragma: no cover - diagnostic only
                errors.append(exc)
                return None
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=count) as pool:
            numbers = list(pool.map(worker, range(count)))

        self.assertEqual(errors, [])
        self.assertNotIn(None, numbers)
        self.assertEqual(len(numbers), len(set(numbers)))
        self.assertEqual(
            set(numbers), {f"INV-{YEAR}-{n:06d}" for n in range(1, count + 1)})
        sequence = DocumentSequence.all_objects.get(
            institution=self.institution, document_type=DocumentType.INVOICE,
            financial_year=str(YEAR))
        self.assertEqual(sequence.last_number, count)


class IssuedDocumentImmutabilityTests(TestCase):
    """An issued document is read-only; corrections go through notes."""

    def setUp(self):
        self.institution = Institution.objects.create(
            name="Immutable College", slug="immutable-college", type="college")
        self.student = make_student(self.institution, "IMM")
        with use_institution(self.institution):
            self.invoice = Invoice.objects.create(
                student=self.student, description="Tuition",
                amount=Decimal("500.00"), due_date=TODAY)

    def test_invoice_cannot_be_changed(self):
        self.invoice.amount = Decimal("600.00")
        with self.assertRaises(ValidationError):
            self.invoice.save()
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount, Decimal("500.00"))

    def test_invoice_cannot_be_deleted(self):
        with self.assertRaises(ValidationError):
            self.invoice.delete()
        self.assertTrue(
            Invoice.all_objects.filter(pk=self.invoice.pk).exists())

    def test_payment_cannot_be_changed_or_deleted(self):
        with use_institution(self.institution):
            payment = Payment.objects.create(
                invoice=self.invoice, amount=Decimal("100.00"))
        payment.amount = Decimal("50.00")
        with self.assertRaises(ValidationError):
            payment.save()
        with self.assertRaises(ValidationError):
            payment.delete()
        self.assertTrue(
            Payment.all_objects.filter(pk=payment.pk).exists())

    def test_credit_note_cannot_be_changed(self):
        with use_institution(self.institution):
            note = Waiver.objects.create(
                invoice=self.invoice, amount=Decimal("50.00"),
                reason="Bursary")
        note.amount = Decimal("10.00")
        with self.assertRaises(ValidationError):
            note.save()

    def test_debit_note_cannot_be_changed(self):
        with use_institution(self.institution):
            note = DebitNote.objects.create(
                invoice=self.invoice, amount=Decimal("20.00"),
                reason="Extra lab fee")
        note.reason = "Changed my mind"
        with self.assertRaises(ValidationError):
            note.save()

    def test_credit_note_reduces_the_balance(self):
        with use_institution(self.institution):
            Waiver.objects.create(
                invoice=self.invoice, amount=Decimal("50.00"),
                reason="Bursary")
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.balance, Decimal("450.00"))

    def test_debit_note_raises_the_balance(self):
        with use_institution(self.institution):
            DebitNote.objects.create(
                invoice=self.invoice, amount=Decimal("25.00"),
                reason="Extra lab fee")
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.balance, Decimal("525.00"))


class VatRateTests(TestCase):
    """VAT follows the dated rate in force and never applies when exempt."""

    def setUp(self):
        self.registered = Institution.objects.create(
            name="VAT College", slug="vat-college", type="college",
            vat_status=Institution.VatStatus.REGISTERED,
            vat_number="1000123", bp_number="2000123")
        self.registered_student = make_student(self.registered, "VAT")
        self.exempt = Institution.objects.create(
            name="Exempt College", slug="exempt-college", type="college")
        self.exempt_student = make_student(self.exempt, "EXM")

    def test_exempt_institution_never_charges_vat(self):
        TaxRate.objects.create(
            institution=self.exempt, name="Standard",
            category=TaxCategory.STANDARD, rate=Decimal("15.50"),
            effective_from=TODAY - timedelta(days=1))
        with use_institution(self.exempt):
            invoice = Invoice.objects.create(
                student=self.exempt_student, description="Tuition",
                amount=Decimal("115.50"), due_date=TODAY,
                tax_category=TaxCategory.STANDARD)
        self.assertEqual(invoice.tax_rate, Decimal("0"))
        self.assertEqual(invoice.tax_amount, Decimal("0"))

    def test_registered_institution_uses_the_rate_in_force_today(self):
        TaxRate.objects.create(
            institution=self.registered, name="Old standard",
            category=TaxCategory.STANDARD, rate=Decimal("15.50"),
            effective_from=TODAY - timedelta(days=90),
            effective_to=TODAY - timedelta(days=1))
        TaxRate.objects.create(
            institution=self.registered, name="Current standard",
            category=TaxCategory.STANDARD, rate=Decimal("20.00"),
            effective_from=TODAY)
        with use_institution(self.registered):
            invoice = Invoice.objects.create(
                student=self.registered_student, description="Tuition",
                amount=Decimal("120.00"), due_date=TODAY,
                tax_category=TaxCategory.STANDARD)
        self.assertEqual(invoice.tax_rate, Decimal("20.00"))
        self.assertEqual(invoice.tax_amount, Decimal("20.00"))
        self.assertEqual(invoice.net_amount, Decimal("100.00"))

    def test_rate_in_force_is_dated(self):
        TaxRate.objects.create(
            institution=self.registered, name="2025",
            category=TaxCategory.STANDARD, rate=Decimal("15.50"),
            effective_from=date(2025, 1, 1), effective_to=date(2025, 12, 31))
        TaxRate.objects.create(
            institution=self.registered, name="2026",
            category=TaxCategory.STANDARD, rate=Decimal("20.00"),
            effective_from=date(2026, 1, 1))
        self.assertEqual(
            services.tax_rate_in_force(
                self.registered, TaxCategory.STANDARD, date(2025, 6, 1)),
            Decimal("15.50"))
        self.assertEqual(
            services.tax_rate_in_force(
                self.registered, TaxCategory.STANDARD, date(2026, 6, 1)),
            Decimal("20.00"))

    def test_registered_without_a_dated_row_uses_the_default_rate(self):
        with use_institution(self.registered):
            invoice = Invoice.objects.create(
                student=self.registered_student, description="Tuition",
                amount=Decimal("115.50"), due_date=TODAY,
                tax_category=TaxCategory.STANDARD)
        self.assertEqual(invoice.tax_rate, Decimal("15.50"))
        self.assertEqual(invoice.tax_amount, Decimal("15.50"))

    def test_exempt_and_zero_categories_never_charge_vat(self):
        with use_institution(self.registered):
            exempt = Invoice.objects.create(
                student=self.registered_student, description="Exempt",
                amount=Decimal("100.00"), due_date=TODAY,
                tax_category=TaxCategory.EXEMPT)
            zero = Invoice.objects.create(
                student=self.registered_student, description="Zero",
                amount=Decimal("100.00"), due_date=TODAY,
                tax_category=TaxCategory.ZERO)
        self.assertEqual(exempt.tax_rate, Decimal("0"))
        self.assertEqual(exempt.tax_amount, Decimal("0"))
        self.assertEqual(zero.tax_rate, Decimal("0"))
        self.assertEqual(zero.tax_amount, Decimal("0"))

    def test_fee_item_category_drives_the_invoice(self):
        with use_institution(self.registered):
            structure = FeeStructure.objects.create(name="Form 1", code="F1")
            item = FeeItem.objects.create(
                structure=structure, name="Tuition", kind="tuition",
                amount=Decimal("115.50"), tax_category=TaxCategory.STANDARD)
            invoice = Invoice.objects.create(
                student=self.registered_student, description="Tuition",
                amount=Decimal("115.50"), due_date=TODAY, fee_item=item)
        self.assertEqual(invoice.tax_category, TaxCategory.STANDARD)
        self.assertEqual(invoice.tax_rate, Decimal("15.50"))
        self.assertEqual(invoice.tax_amount, Decimal("15.50"))

    def test_vat_summary_nets_output_against_input(self):
        TaxRate.objects.create(
            institution=self.registered, name="Standard",
            category=TaxCategory.STANDARD, rate=Decimal("20.00"),
            effective_from=TODAY)
        with use_institution(self.registered):
            Invoice.objects.create(
                student=self.registered_student, description="Tuition",
                amount=Decimal("120.00"), due_date=TODAY,
                tax_category=TaxCategory.STANDARD)
            Expense.objects.create(
                description="Electricity", amount=Decimal("60.00"),
                tax_category=TaxCategory.STANDARD, date=TODAY)
        data = services.vat_summary()
        self.assertEqual(data["output_vat"], Decimal("20.00"))
        self.assertEqual(data["input_vat"], Decimal("10.00"))
        self.assertEqual(data["net_vat"], Decimal("10.00"))
