"""Production-hardening regression tests for money and payment callbacks."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse

from academics.models import Programme, Student
from core.models import Institution
from core.tenancy import use_institution
from finance import services
from finance.gateways import PaynowGateway
from finance.models import (
    GatewayTransaction,
    Invoice,
    Payment,
    Refund,
    StudentCredit,
)

User = get_user_model()


class RefundApprovalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            "hardening-admin", password="pw123456", role="admin",
            is_staff=True, is_superuser=True,
        )
        cls.programme = Programme.objects.create(
            name="Diploma in IT", code="DIT", level="diploma")
        cls.student_user = User.objects.create_user(
            "hardening-student", password="pw123456", role="student")
        cls.student = Student.objects.create(
            user=cls.student_user, programme=cls.programme, student_no="H9001")

    def _payment(self, amount="100.00"):
        invoice = Invoice.objects.create(
            student=self.student, description="Tuition",
            amount=Decimal(amount), due_date=date.today())
        payment, _credit = services.receive_payment(
            self.student, invoice, Decimal(amount), received_by=self.admin)
        return payment

    def test_pending_refunds_cannot_exceed_the_original_payment(self):
        payment = self._payment()
        services.request_refund(payment, Decimal("60.00"), "First request")
        with self.assertRaises(ValidationError):
            services.request_refund(payment, Decimal("50.00"), "Second request")
        self.assertEqual(payment.refunds.count(), 1)

    def test_approval_rechecks_the_remaining_payment_balance(self):
        payment = self._payment()
        first = services.request_refund(payment, Decimal("60.00"), "First request")
        # Model.clean only considers approved refunds, so a second pending row
        # can be created directly. Approval must still refuse to over-refund.
        second = Refund.objects.create(
            payment=payment, amount=Decimal("50.00"), reason="Second request")
        services.approve_refund(first, self.admin)
        with self.assertRaises(ValidationError):
            services.approve_refund(second, self.admin)
        second.refresh_from_db()
        self.assertEqual(second.status, Refund.Status.PENDING)
        self.assertEqual(payment.net_amount, Decimal("40.00"))


class GatewayIdempotencyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.programme = Programme.objects.create(
            name="Diploma in IT", code="DIT", level="diploma")
        cls.student_user = User.objects.create_user(
            "gateway-student", password="pw123456", role="student")
        cls.student = Student.objects.create(
            user=cls.student_user, programme=cls.programme, student_no="G9001")

    def _invoice(self, amount="100.00", institution=None):
        return Invoice.objects.create(
            student=self.student, description="Tuition",
            amount=Decimal(amount), due_date=date.today(),
            institution=institution or self.student.institution)

    def _gateway(self):
        return PaynowGateway(integration_id="123", integration_key="secret")

    def _post_webhook(self, reference, gateway, status="Paid", client=None):
        payload = {"reference": reference, "status": status}
        payload["hash"] = gateway._hash([reference, status])
        return (client or self.client).post(reverse("paynow_webhook"), payload)

    def test_webhook_is_csrf_exempt_and_processes_a_signed_callback(self):
        invoice = self._invoice()
        GatewayTransaction.objects.create(
            reference="ON-HARDEN-1", idempotency_key="hardening-1",
            amount=Decimal("100.00"), invoice=invoice,
            currency=invoice.currency)
        gateway = self._gateway()
        with patch("finance.gateways.get_gateway", return_value=gateway):
            response = self._post_webhook(
                "ON-HARDEN-1", gateway, client=Client(enforce_csrf_checks=True))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        transaction_row = GatewayTransaction.objects.get(reference="ON-HARDEN-1")
        self.assertEqual(transaction_row.status, GatewayTransaction.Status.PAID)
        self.assertEqual(invoice.balance, Decimal("0.00"))

    def test_a_duplicate_paid_webhook_posts_the_payment_only_once(self):
        invoice = self._invoice()
        GatewayTransaction.objects.create(
            reference="ON-HARDEN-2", idempotency_key="hardening-2",
            amount=Decimal("100.00"), invoice=invoice,
            currency=invoice.currency)
        gateway = self._gateway()
        with patch("finance.gateways.get_gateway", return_value=gateway):
            self.assertEqual(self._post_webhook("ON-HARDEN-2", gateway).status_code, 200)
            self.assertEqual(self._post_webhook("ON-HARDEN-2", gateway).status_code, 200)
        self.assertEqual(Payment.objects.filter(invoice=invoice).count(), 1)
        self.assertFalse(StudentCredit.objects.filter(student=self.student).exists())
        self.assertEqual(
            GatewayTransaction.objects.filter(reference="ON-HARDEN-2").count(), 1)

    def test_webhook_finds_a_transaction_in_another_institution(self):
        other = Institution.objects.create(
            name="Other Gateway School", slug="other-gateway", type="college")
        with use_institution(other):
            programme = Programme.objects.create(
                name="Grade 7", code="G7", level="grade")
            user = User.objects.create_user(
                "other-gateway-student", password="pw123456",
                role="student", institution=other)
            student = Student.objects.create(
                user=user, programme=programme, student_no="OG1", institution=other)
            invoice = Invoice.objects.create(
                student=student, description="Tuition", amount=Decimal("80.00"),
                due_date=date.today(), institution=other)
            GatewayTransaction.objects.create(
                reference="ON-HARDEN-3", idempotency_key="hardening-3",
                amount=Decimal("80.00"), invoice=invoice,
                currency=invoice.currency)
        gateway = self._gateway()
        with patch("finance.gateways.get_gateway", return_value=gateway):
            response = self._post_webhook("ON-HARDEN-3", gateway)
        self.assertEqual(response.status_code, 200)
        row = GatewayTransaction.all_objects.get(reference="ON-HARDEN-3")
        self.assertEqual(row.status, GatewayTransaction.Status.PAID)
        self.assertEqual(Payment.all_objects.filter(invoice=invoice).count(), 1)

    def test_gateway_references_are_unique(self):
        invoice = self._invoice()
        GatewayTransaction.objects.create(
            reference="ON-DUPLICATE", idempotency_key="hardening-4",
            amount=Decimal("10.00"), invoice=invoice)
        with self.assertRaises(IntegrityError), transaction.atomic():
            GatewayTransaction.objects.create(
                reference="ON-DUPLICATE", idempotency_key="hardening-5",
                amount=Decimal("10.00"), invoice=invoice)

    def test_a_currency_mismatch_never_posts_cash(self):
        invoice = self._invoice()
        invoice.currency = "USD"
        invoice.save(update_fields=["currency"])
        GatewayTransaction.objects.create(
            reference="ON-HARDEN-6", idempotency_key="hardening-6",
            amount=Decimal("100.00"), invoice=invoice, currency="ZAR")
        gateway = self._gateway()
        with patch("finance.gateways.get_gateway", return_value=gateway):
            response = self._post_webhook("ON-HARDEN-6", gateway)
        self.assertEqual(response.status_code, 502)
        self.assertFalse(Payment.objects.filter(invoice=invoice).exists())
