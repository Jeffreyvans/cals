from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from academics.models import Programme, Student
from finance.forms import InvoiceForm, PaymentForm
from finance.models import Invoice, Payment

User = get_user_model()


class PaymentFormTests(TestCase):
    def setUp(self):
        self.bursar = User.objects.create_user("bur", password="pw123456", role="bursar")
        programme = Programme.objects.create(name="Diploma in IT", code="DIT", level="diploma")
        u1 = User.objects.create_user("s1", password="pw123456", role="student")
        u2 = User.objects.create_user("s2", password="pw123456", role="student")
        self.student = Student.objects.create(user=u1, programme=programme, student_no="S0401")
        self.other = Student.objects.create(user=u2, programme=programme, student_no="S0402")
        self.invoice = Invoice.objects.create(student=self.student, description="Tuition",
                                              amount=Decimal("500.00"), due_date=date.today())
        self.other_invoice = Invoice.objects.create(student=self.other, description="Tuition",
                                                    amount=Decimal("500.00"),
                                                    due_date=date.today())

    def form(self, **data):
        return PaymentForm(self.student, data=data)

    def test_overpayment_is_rejected(self):
        form = self.form(invoice=self.invoice.pk, amount="500.01", method="cash")
        self.assertFalse(form.is_valid())
        self.assertIn("exceeds", str(form.errors))
        self.assertEqual(Payment.objects.count(), 0)

    def test_zero_and_negative_amounts_are_rejected(self):
        for value in ("0", "-10"):
            form = self.form(invoice=self.invoice.pk, amount=value, method="cash")
            self.assertFalse(form.is_valid(), value)
        self.assertEqual(Payment.objects.count(), 0)

    def test_payment_cannot_target_another_students_invoice(self):
        form = self.form(invoice=self.other_invoice.pk, amount="50", method="cash")
        self.assertFalse(form.is_valid())
        self.assertIn("invoice", form.errors)
        self.assertEqual(Payment.objects.count(), 0)

    def test_exact_and_partial_payments_are_accepted(self):
        form = self.form(invoice=self.invoice.pk, amount="500", method="bank",
                         reference="NMB123")
        self.assertTrue(form.is_valid(), form.errors)
        payment = form.save()
        self.assertEqual(payment.invoice.balance, Decimal("0"))
        self.assertTrue(payment.receipt_no.startswith("RC"))
        second = self.form(invoice=self.invoice.pk, amount="1", method="cash")
        self.assertFalse(second.is_valid())

    def test_invoice_form_rejects_zero_amount(self):
        f = InvoiceForm(data={"description": "Trip", "amount": "0",
                              "due_date": "2026-02-01"})
        self.assertFalse(f.is_valid())
        self.assertIn("amount", f.errors)


class PaymentModelTests(TestCase):
    def setUp(self):
        programme = Programme.objects.create(name="Diploma in IT", code="DIT", level="diploma")
        user = User.objects.create_user("s1", password="pw123456", role="student")
        self.student = Student.objects.create(user=user, programme=programme, student_no="S0411")
        self.invoice = Invoice.objects.create(student=self.student, description="Tuition",
                                              amount=Decimal("100.00"), due_date=date.today())

    def test_model_blocks_overpayment(self):
        with self.assertRaises(ValidationError):
            Payment.objects.create(invoice=self.invoice, amount=Decimal("150.00"))
        self.assertEqual(self.invoice.payments.count(), 0)

    def test_sequential_payments_stop_at_the_balance(self):
        Payment.objects.create(invoice=self.invoice, amount=Decimal("70.00"))
        Payment.objects.create(invoice=self.invoice, amount=Decimal("30.00"))
        self.assertEqual(self.invoice.balance, Decimal("0.00"))
        with self.assertRaises(ValidationError):
            Payment.objects.create(invoice=self.invoice, amount=Decimal("0.01"))

    def test_receipt_number_is_unique_and_generated(self):
        p1 = Payment.objects.create(invoice=self.invoice, amount=Decimal("10.00"))
        p2 = Payment.objects.create(invoice=self.invoice, amount=Decimal("20.00"))
        self.assertTrue(p1.receipt_no)
        self.assertTrue(p2.receipt_no)
        self.assertNotEqual(p1.receipt_no, p2.receipt_no)


class ReceiptViewTests(TestCase):
    def setUp(self):
        programme = Programme.objects.create(name="Diploma in IT", code="DIT", level="diploma")
        u1 = User.objects.create_user("s1", password="pw123456", role="student")
        u2 = User.objects.create_user("s2", password="pw123456", role="student")
        self.student = Student.objects.create(user=u1, programme=programme, student_no="S0421")
        other = Student.objects.create(user=u2, programme=programme, student_no="S0422")
        inv = Invoice.objects.create(student=self.student, description="Tuition",
                                     amount=Decimal("100.00"), due_date=date.today())
        other_inv = Invoice.objects.create(student=other, description="Tuition",
                                           amount=Decimal("100.00"), due_date=date.today())
        self.payment = Payment.objects.create(invoice=inv, amount=Decimal("100.00"))
        self.other_payment = Payment.objects.create(invoice=other_inv,
                                                    amount=Decimal("100.00"))

    def test_student_reads_only_own_receipts(self):
        self.client.force_login(self.student.user)
        own = self.client.get(reverse("receipt", args=[self.payment.pk]))
        self.assertEqual(own.status_code, 200)
        self.assertContains(own, self.payment.receipt_no)
        forbidden = self.client.get(reverse("receipt", args=[self.other_payment.pk]))
        self.assertEqual(forbidden.status_code, 403)

    def test_bursar_can_read_any_receipt(self):
        self.client.force_login(User.objects.create_user("bur", password="pw123456",
                                                         role="bursar"))
        resp = self.client.get(reverse("receipt", args=[self.other_payment.pk]))
        self.assertEqual(resp.status_code, 200)
