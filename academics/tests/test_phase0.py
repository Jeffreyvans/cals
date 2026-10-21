from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from academics.models import Student, Programme, Term, AcademicYear
from finance.models import Invoice, Payment, Waiver


class Phase0PermissionTests(TestCase):
    def setUp(self):
        p = Programme.objects.create(name="Test Prog", code="TP", level="diploma")
        ay = AcademicYear.objects.create(name="2026", start_date="2026-01-01", end_date="2026-12-31")
        Term.objects.create(academic_year=ay, name="Term 1", start_date="2026-01-01", end_date="2026-04-01")
        u1 = User.objects.create_user("s1", password="x")
        u2 = User.objects.create_user("s2", password="x")
        self.st1 = Student.objects.create(user=u1, programme=p, student_no="202600001")
        self.st2 = Student.objects.create(user=u2, programme=p, student_no="202600002")

    def test_student_cannot_see_another_students_detail(self):
        self.client.login(username="s1", password="x")
        resp = self.client.get(reverse("student_detail", args=[self.st2.pk]))
        self.assertEqual(resp.status_code, 403)

    def test_student_cannot_see_another_students_receipt(self):
        inv = Invoice.objects.create(student=self.st2, description="Fee", amount=Decimal("100"), due_date="2026-03-01")
        pay = Payment.objects.create(invoice=inv, amount=Decimal("50"), method="cash")
        self.client.login(username="s1", password="x")
        resp = self.client.get(reverse("receipt", args=[pay.pk]))
        self.assertEqual(resp.status_code, 403)


class Phase0PaymentRuleTests(TestCase):
    def setUp(self):
        p = Programme.objects.create(name="Test Prog", code="TP2", level="diploma")
        u = User.objects.create_user("s1", password="x")
        self.st = Student.objects.create(user=u, programme=p, student_no="202600011")
        self.inv = Invoice.objects.create(student=self.st, description="Fee", amount=Decimal("100"), due_date="2026-03-01")

    def test_no_overpayment(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            Payment.objects.create(invoice=self.inv, amount=Decimal("150"), method="cash")


class Phase0BalanceCalcTests(TestCase):
    def setUp(self):
        p = Programme.objects.create(name="Test Prog", code="TP3", level="diploma")
        u = User.objects.create_user("s1", password="x")
        self.st = Student.objects.create(user=u, programme=p, student_no="202600021")

    def test_balance_calculation_exact(self):
        inv1 = Invoice.objects.create(student=self.st, description="Fee1", amount=Decimal("200"), due_date="2026-03-01")
        inv2 = Invoice.objects.create(student=self.st, description="Fee2", amount=Decimal("50"), due_date="2026-04-01")
        Payment.objects.create(invoice=inv1, amount=Decimal("80"), method="cash")
        Payment.objects.create(invoice=inv2, amount=Decimal("30"), method="mobile")
        Waiver.objects.create(invoice=inv1, amount=Decimal("20"), reason="bursary")
        self.assertEqual(self.st.total_billed, Decimal("250"))
        self.assertEqual(self.st.total_paid, Decimal("110"))
        self.assertEqual(self.st.total_waived, Decimal("20"))
        self.assertEqual(self.st.balance, Decimal("120"))
