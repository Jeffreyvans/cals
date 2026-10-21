from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from academics.models import Programme, Student, balances_by_student
from finance.models import Invoice, Payment, Waiver

User = get_user_model()


class BalanceTests(TestCase):
    def setUp(self):
        self.programme = Programme.objects.create(name="Diploma in IT", code="DIT",
                                                  level="diploma", tuition_fee=500)
        self.user = User.objects.create_user("stu", password="pw123456", role="student")
        self.student = Student.objects.create(user=self.user, programme=self.programme,
                                              student_no="S0100")

    def test_student_with_no_invoices_owes_nothing(self):
        self.assertEqual(self.student.total_billed, Decimal("0"))
        self.assertEqual(self.student.total_paid, Decimal("0"))
        self.assertEqual(self.student.balance, Decimal("0"))

    def test_balance_is_billed_minus_paid(self):
        inv = Invoice.objects.create(student=self.student, description="Tuition",
                                     amount=Decimal("1000.00"), due_date=date.today())
        self.assertEqual(self.student.total_billed, Decimal("1000.00"))
        self.assertEqual(self.student.balance, Decimal("1000.00"))
        Payment.objects.create(invoice=inv, amount=Decimal("400.00"))
        self.assertEqual(self.student.total_paid, Decimal("400.00"))
        self.assertEqual(self.student.balance, Decimal("600.00"))

    def test_balance_is_net_of_waivers(self):
        inv = Invoice.objects.create(student=self.student, description="Tuition",
                                     amount=Decimal("500.00"), due_date=date.today())
        Waiver.objects.create(invoice=inv, amount=Decimal("200.00"), reason="Bursary")
        self.assertEqual(inv.waived, Decimal("200.00"))
        self.assertEqual(inv.balance, Decimal("300.00"))
        self.assertEqual(self.student.total_waived, Decimal("200.00"))
        self.assertEqual(self.student.balance, Decimal("300.00"))
        Payment.objects.create(invoice=inv, amount=Decimal("300.00"))
        self.assertEqual(self.student.balance, Decimal("0"))

    def test_balance_across_several_invoices_and_payments(self):
        a = Invoice.objects.create(student=self.student, description="Tuition",
                                   amount=Decimal("500.00"), due_date=date.today())
        b = Invoice.objects.create(student=self.student, description="Hostel",
                                   amount=Decimal("250.00"), due_date=date.today())
        Payment.objects.create(invoice=a, amount=Decimal("500.00"))
        Payment.objects.create(invoice=b, amount=Decimal("100.00"))
        self.assertEqual(self.student.total_billed, Decimal("750.00"))
        self.assertEqual(self.student.total_paid, Decimal("600.00"))
        self.assertEqual(self.student.balance, Decimal("150.00"))

    def test_invoice_paid_and_balance(self):
        inv = Invoice.objects.create(student=self.student, description="Tuition",
                                     amount=Decimal("300.00"), due_date=date.today())
        self.assertEqual(inv.paid, Decimal("0"))
        self.assertEqual(inv.balance, Decimal("300.00"))
        Payment.objects.create(invoice=inv, amount=Decimal("120.50"))
        self.assertEqual(inv.paid, Decimal("120.50"))
        self.assertEqual(inv.balance, Decimal("179.50"))

    def test_payment_cannot_exceed_invoice_balance(self):
        from django.core.exceptions import ValidationError
        inv = Invoice.objects.create(student=self.student, description="Tuition",
                                     amount=Decimal("100.00"), due_date=date.today())
        with self.assertRaises(ValidationError):
            Payment.objects.create(invoice=inv, amount=Decimal("100.01"))
        with self.assertRaises(ValidationError):
            Payment.objects.create(invoice=inv, amount=Decimal("0"))
        self.assertEqual(inv.payments.count(), 0)

    def test_student_number_is_generated(self):
        user = User.objects.create_user("stu2", password="pw123456", role="student")
        s = Student.objects.create(user=user, programme=self.programme)
        self.assertTrue(s.student_no)
        self.assertNotEqual(s.student_no, "")

    def test_balances_by_student_matches_properties(self):
        other_user = User.objects.create_user("stu3", password="pw123456", role="student")
        other = Student.objects.create(user=other_user, programme=self.programme,
                                       student_no="S0101")
        inv1 = Invoice.objects.create(student=self.student, description="Tuition",
                                      amount=Decimal("300.00"), due_date=date.today())
        Invoice.objects.create(student=other, description="Tuition",
                               amount=Decimal("300.00"), due_date=date.today())
        Payment.objects.create(invoice=inv1, amount=Decimal("100.00"))
        Waiver.objects.create(invoice=inv1, amount=Decimal("50.00"), reason="Bursary")
        billed, paid, waived = balances_by_student()
        for s in (self.student, other):
            self.assertEqual(billed.get(s.pk, Decimal("0")) - paid.get(s.pk, Decimal("0"))
                             - waived.get(s.pk, Decimal("0")), s.balance)
