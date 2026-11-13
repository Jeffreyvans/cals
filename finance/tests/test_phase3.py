from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from academics.models import AcademicYear, Programme, Student, Term
from finance import services
from finance.gateways import GatewayError, PaynowGateway, start_online_payment
from finance.models import (
    Account,
    BankStatementLine,
    FeeItem,
    FeeStructure,
    FinancialYear,
    GatewayTransaction,
    Instalment,
    InstalmentPlan,
    Invoice,
    JournalLine,
    LatePenaltyRule,
    Refund,
)

User = get_user_model()


class FinanceBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            "fin-admin", password="pw123456", role="admin",
            is_staff=True, is_superuser=True)
        cls.programme = Programme.objects.create(
            name="Diploma in IT", code="DIT", level="diploma")
        cls.student_user = User.objects.create_user(
            "fin-student", password="pw123456", role="student")
        cls.student = Student.objects.create(
            user=cls.student_user, programme=cls.programme,
            student_no="S9001")
        cls.year = AcademicYear.objects.create(
            name="2026", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
            is_current=True)
        cls.term = Term.objects.create(
            academic_year=cls.year, name="Term 1",
            start_date=date(2026, 1, 1), end_date=date(2026, 4, 30),
            is_current=True)

    def invoice(self, amount="100.00", student=None, description="Tuition"):
        return Invoice.objects.create(
            student=student or self.student, description=description,
            amount=Decimal(amount), due_date=date.today(), term=self.term)


class ChartAndInvoiceTests(FinanceBase):
    def test_chart_of_accounts_is_idempotent(self):
        institution = self.student.institution
        first = services.ensure_chart_of_accounts(institution)
        second = services.ensure_chart_of_accounts(institution)
        self.assertGreater(first, 0)
        self.assertEqual(second, 0)
        self.assertTrue(Account.objects.filter(institution=institution).exists())

    def test_generate_invoices_previews_then_creates_once(self):
        structure = FeeStructure.objects.create(
            code="DIT-2026", name="DIT 2026", academic_year=self.year,
            programme=self.programme, term=self.term)
        FeeItem.objects.create(structure=structure, name="Tuition",
                               amount=Decimal("500.00"))
        FeeItem.objects.create(structure=structure, name="Levy",
                               amount=Decimal("50.00"))

        preview = services.generate_invoices(structure, self.term, preview=True)
        self.assertEqual(preview["created"], 0)
        self.assertEqual(len(preview["rows"]), 2)
        self.assertEqual(Invoice.objects.count(), 0)

        made = services.generate_invoices(structure, self.term, preview=False)
        self.assertEqual(made["created"], 2)
        self.assertEqual(Invoice.objects.count(), 2)
        self.assertEqual(made["total"], Decimal("550.00"))

        again = services.generate_invoices(structure, self.term, preview=False)
        self.assertEqual(again["created"], 0)
        self.assertEqual(Invoice.objects.count(), 2)

    def test_invoice_posts_a_balanced_journal(self):
        self.invoice()
        debit = sum(line.debit for line in JournalLine.objects.all())
        credit = sum(line.credit for line in JournalLine.objects.all())
        self.assertEqual(debit, credit)
        self.assertEqual(debit, Decimal("100.00"))


class PaymentCreditAndRefundTests(FinanceBase):
    def test_payment_and_overpayment_are_split(self):
        invoice = self.invoice()
        payment, credit = services.receive_payment(
            self.student, invoice, Decimal("120.00"), method="cash")
        self.assertEqual(payment.amount, Decimal("100.00"))
        self.assertEqual(credit.amount, Decimal("20.00"))
        self.assertEqual(invoice.balance, Decimal("0.00"))
        self.assertEqual(services.student_balance(self.student), Decimal("0.00"))
        self.assertEqual(services.credit_balance(self.student), Decimal("20.00"))
        debit = sum(line.debit for line in JournalLine.objects.all())
        credit_total = sum(line.credit for line in JournalLine.objects.all())
        self.assertEqual(debit, credit_total)

    def test_credit_can_be_applied_to_another_invoice(self):
        first = self.invoice()
        _payment, credit = services.receive_payment(
            self.student, first, Decimal("120.00"))
        second = self.invoice("50.00", description="Levy")
        services.apply_credit(credit, second)
        self.assertEqual(second.balance, Decimal("30.00"))
        self.assertEqual(services.credit_balance(self.student), Decimal("0.00"))

    def test_discount_then_refund_updates_the_balance(self):
        invoice = self.invoice()
        discount = services.Discount.objects.create(
            student=self.student, invoice=invoice, name="Sibling bursary",
            category="bursary", kind="percentage", value=Decimal("10"))
        services.apply_discount(discount)
        self.assertEqual(discount.amount, Decimal("10.00"))
        payment, _credit = services.receive_payment(
            self.student, invoice, Decimal("90.00"))
        refund = services.request_refund(payment, Decimal("20.00"), "Overpaid")
        services.approve_refund(refund, self.admin)
        self.assertEqual(invoice.refunded, Decimal("20.00"))
        self.assertEqual(invoice.balance, Decimal("20.00"))

    def test_refund_cannot_exceed_the_payment(self):
        invoice = self.invoice()
        payment, _credit = services.receive_payment(
            self.student, invoice, Decimal("50.00"))
        with self.assertRaises(ValidationError):
            Refund.objects.create(
                payment=payment, amount=Decimal("80.00"), reason="Too much")


class PenaltyAndYearTests(FinanceBase):
    def test_late_penalty_is_applied_only_once(self):
        plan = InstalmentPlan.objects.create(
            student=self.student, term=self.term, total_amount=Decimal("100.00"))
        Instalment.objects.create(
            plan=plan, due_date=date.today() - timedelta(days=30),
            amount=Decimal("100.00"))
        rule = LatePenaltyRule.objects.create(
            name="Late fee", kind="percentage", value=Decimal("10"),
            grace_days=5)
        first = services.apply_late_penalties(rule)
        second = services.apply_late_penalties(rule)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 0)
        self.assertEqual(Invoice.objects.filter(
            description__startswith="Late penalty").count(), 1)

    def test_closed_financial_year_blocks_posting(self):
        year = FinancialYear.objects.create(
            name="FY2026", start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31))
        services.close_financial_year(year, self.admin)
        with self.assertRaises(ValidationError):
            services.post_journal(
                self.student.institution, "Blocked",
                [("1000", Decimal("1"), 0, "test"),
                 ("4000", 0, Decimal("1"), "test")])


class ReportsAndBankTests(FinanceBase):
    def test_trial_balance_and_balance_sheet_agree(self):
        invoice = self.invoice("100.00")
        services.receive_payment(self.student, invoice, Decimal("40.00"))
        from finance.models import Expense, Supplier

        supplier = Supplier.objects.create(name="Paper Co")
        Expense.objects.create(
            supplier=supplier, description="Paper", amount=Decimal("15.00"),
            date=date.today())
        trial = services.trial_balance()
        self.assertEqual(trial["total_debit"], trial["total_credit"])
        sheet = services.balance_sheet()
        self.assertEqual(
            sheet["total_assets"],
            sheet["total_liabilities"] + sheet["total_equity"])

    def test_bank_csv_import_and_auto_match(self):
        invoice = self.invoice()
        payment, _credit = services.receive_payment(
            self.student, invoice, Decimal("25.00"), reference="BANK-1")
        text = "date,description,amount,reference\n2026-02-01,Deposit,25.00,BANK-1\n"
        made = services.import_bank_statement(text)
        self.assertEqual(len(made), 1)
        matched = services.match_bank_lines()
        self.assertEqual(matched, 1)
        line = BankStatementLine.objects.get()
        self.assertEqual(line.matched_payment, payment)


class GatewayTests(FinanceBase):
    def test_signature_round_trip(self):
        gateway = PaynowGateway(integration_id="123", integration_key="secret")
        payload = {"reference": "ON1", "status": "Paid"}
        signature = gateway._hash(["ON1", "Paid"])
        self.assertTrue(gateway.verify_signature(payload, signature))
        self.assertFalse(gateway.verify_signature(payload, "bad"))

    @patch("finance.gateways._post",
           return_value="status=Ok&browserurl=https%3A%2F%2Fpaynow.test%2Fpay")
    def test_start_payment_returns_redirect(self, _post):
        invoice = self.invoice()
        gateway = PaynowGateway(integration_id="123", integration_key="secret")
        with patch("finance.gateways.get_gateway", return_value=gateway):
            transaction, redirect = start_online_payment(
                invoice, Decimal("100.00"), "key-1")
        self.assertEqual(redirect, "https://paynow.test/pay")
        self.assertEqual(transaction.status, GatewayTransaction.Status.PENDING)

    def test_paid_webhook_creates_payment(self):
        invoice = self.invoice()
        gateway = PaynowGateway(integration_id="123", integration_key="secret")
        transaction = GatewayTransaction.objects.create(
            reference="ON-WEBHOOK", idempotency_key="webhook-1",
            amount=Decimal("100.00"), invoice=invoice)
        payload = {"reference": "ON-WEBHOOK", "status": "Paid"}
        signature = gateway._hash(["ON-WEBHOOK", "Paid"])
        gateway.handle_webhook(payload, signature)
        transaction.refresh_from_db()
        self.assertEqual(transaction.status, GatewayTransaction.Status.PAID)
        self.assertIsNotNone(transaction.payment)
        self.assertEqual(invoice.balance, Decimal("0.00"))

    @patch("finance.gateways._post",
           return_value="status=Ok&browserurl=https%3A%2F%2Fpaynow.test%2Fpay")
    def test_start_payment_is_idempotent(self, _post):
        invoice = self.invoice()
        gateway = PaynowGateway(integration_id="123", integration_key="secret")
        with patch("finance.gateways.get_gateway", return_value=gateway):
            first, first_url = start_online_payment(
                invoice, Decimal("100.00"), "same-key")
            second, second_url = start_online_payment(
                invoice, Decimal("100.00"), "same-key")
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first_url, second_url)
        self.assertEqual(GatewayTransaction.objects.count(), 1)

    def test_unconfigured_gateway_refuses_to_start(self):
        invoice = self.invoice()
        with self.assertRaises(GatewayError):
            start_online_payment(invoice, Decimal("10.00"), "key-2")


class FinanceViewTests(FinanceBase):
    def test_new_finance_screens_render(self):
        structure = FeeStructure.objects.create(
            code="VIEW-1", name="View fees", academic_year=self.year,
            programme=self.programme)
        FeeItem.objects.create(structure=structure, name="Tuition",
                               amount=Decimal("100.00"))
        invoice = self.invoice()
        payment, _credit = services.receive_payment(
            self.student, invoice, Decimal("10.00"))
        services.request_refund(payment, Decimal("1.00"), "Test")
        self.client.force_login(self.admin)
        urls = [
            reverse("fee_structures"),
            reverse("fee_structure_detail", args=[structure.pk]),
            reverse("generate_invoices"),
            reverse("accounts"),
            reverse("journal_list"),
            reverse("financial_years"),
            reverse("trial_balance"),
            reverse("income_statement"),
            reverse("balance_sheet"),
            reverse("discount_list"),
            reverse("sponsor_list"),
            reverse("instalment_plans"),
            reverse("overdue_instalments"),
            reverse("penalty_rules"),
            reverse("refund_list"),
            reverse("supplier_list"),
            reverse("expense_list"),
            reverse("budget_list"),
            reverse("bank_lines"),
            reverse("bank_import"),
            reverse("report_income_by_item"),
            reverse("report_refunds"),
            reverse("report_expenses"),
            reverse("report_cashup"),
            reverse("student_statement", args=[self.student.pk]),
            reverse("student_statement_pdf", args=[self.student.pk]),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_public_receipt_verification(self):
        invoice = self.invoice()
        payment, _credit = services.receive_payment(
            self.student, invoice, Decimal("10.00"))
        response = self.client.get(
            reverse("verify_receipt", args=[payment.verification_token]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, payment.receipt_no)
