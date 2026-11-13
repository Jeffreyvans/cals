from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.factories import UserFactory
from core.models import Institution
from core.modules import set_module
from core.roles import ensure_roles
from finance.models import Expense, JournalEntry, Supplier
from operations.models import (
    PurchaseOrder,
    PurchaseOrderLine,
    Quotation,
    QuotationLine,
    Requisition,
    SupplierInvoice,
)
from operations.services import (
    award_quotation,
    match_supplier_invoice,
    supplier_invoice_expected_total,
)


class ProcurementFollowUpTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.institution = Institution.objects.get(slug="default")
        ensure_roles(cls.institution)
        set_module(cls.institution, "procurement_stores", True)
        cls.admin = UserFactory(
            username="procurement_admin", role="admin", institution=cls.institution
        )
        cls.supplier = Supplier.objects.create(
            institution=cls.institution, name="Stationery Ltd"
        )
        cls.other_supplier = Supplier.objects.create(
            institution=cls.institution, name="Paper Co"
        )

    def _requisition(self, status=Requisition.Status.APPROVED):
        return Requisition.objects.create(
            institution=self.institution, title="Exercise books", status=status
        )

    def _order(self, total="100.00"):
        order = PurchaseOrder.objects.create(
            institution=self.institution,
            supplier=self.supplier,
            order_date=date(2026, 2, 1),
            status=PurchaseOrder.Status.APPROVED,
        )
        PurchaseOrderLine.objects.create(
            institution=self.institution,
            order=order,
            description="Exercise books",
            quantity=1,
            unit_price=Decimal(total),
        )
        return order

    def test_awarding_a_quotation_rejects_the_others_and_raises_an_order(self):
        requisition = self._requisition()
        winner = Quotation.objects.create(
            institution=self.institution,
            requisition=requisition,
            supplier=self.supplier,
            amount=Decimal("90.00"),
        )
        QuotationLine.objects.create(
            institution=self.institution,
            quotation=winner,
            description="Exercise books",
            quantity=3,
            unit_price=Decimal("30.00"),
        )
        loser = Quotation.objects.create(
            institution=self.institution,
            requisition=requisition,
            supplier=self.other_supplier,
            amount=Decimal("120.00"),
        )
        order = award_quotation(winner, self.admin)
        winner.refresh_from_db()
        loser.refresh_from_db()
        requisition.refresh_from_db()
        self.assertEqual(winner.status, Quotation.Status.AWARDED)
        self.assertEqual(winner.awarded_by, self.admin)
        self.assertIsNotNone(winner.awarded_at)
        self.assertEqual(loser.status, Quotation.Status.REJECTED)
        self.assertEqual(requisition.status, Requisition.Status.ORDERED)
        self.assertEqual(order.supplier, self.supplier)
        self.assertEqual(order.requisition, requisition)
        self.assertEqual(order.status, PurchaseOrder.Status.APPROVED)
        self.assertEqual(order.total, Decimal("90.00"))
        self.assertEqual(order.lines.get().quantity, 3)
        with self.assertRaises(ValidationError):
            award_quotation(winner, self.admin)

    def test_a_quotation_can_only_be_awarded_for_an_approved_requisition(self):
        requisition = self._requisition(Requisition.Status.DRAFT)
        quotation = Quotation.objects.create(
            institution=self.institution,
            requisition=requisition,
            supplier=self.supplier,
            amount=Decimal("50.00"),
        )
        with self.assertRaises(ValidationError):
            award_quotation(quotation, self.admin)

    def test_matching_an_invoice_posts_an_expense_when_the_totals_agree(self):
        order = self._order("100.00")
        invoice = SupplierInvoice.objects.create(
            institution=self.institution,
            supplier=self.supplier,
            order=order,
            number="INV-001",
            invoice_date=date(2026, 2, 2),
            amount=Decimal("100.00"),
        )
        self.assertEqual(supplier_invoice_expected_total(invoice), Decimal("100.00"))
        match_supplier_invoice(invoice, self.admin)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, SupplierInvoice.Status.MATCHED)
        self.assertEqual(invoice.variance, Decimal("0.00"))
        self.assertEqual(invoice.matched_by, self.admin)
        self.assertIsNotNone(invoice.expense)
        self.assertEqual(invoice.expense.amount, Decimal("100.00"))
        self.assertTrue(
            JournalEntry.all_objects.filter(
                institution=self.institution, source_type="Expense", source_id=str(invoice.expense_id)
            ).exists()
        )
        with self.assertRaises(ValidationError):
            match_supplier_invoice(invoice, self.admin)

    def test_a_difference_marks_the_invoice_disputed_and_posts_nothing(self):
        order = self._order("100.00")
        invoice = SupplierInvoice.objects.create(
            institution=self.institution,
            supplier=self.supplier,
            order=order,
            number="INV-002",
            amount=Decimal("120.00"),
        )
        match_supplier_invoice(invoice, self.admin)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, SupplierInvoice.Status.DISPUTED)
        self.assertEqual(invoice.variance, Decimal("20.00"))
        self.assertIsNone(invoice.expense)
        self.assertIn("differs", invoice.match_note)
        self.assertFalse(
            Expense.objects.filter(institution=self.institution, supplier=self.supplier).exists()
        )

    def test_an_invoice_without_an_order_is_matched_at_face_value(self):
        invoice = SupplierInvoice.objects.create(
            institution=self.institution,
            supplier=self.supplier,
            number="INV-003",
            amount=Decimal("45.50"),
        )
        match_supplier_invoice(invoice, self.admin)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, SupplierInvoice.Status.MATCHED)
        self.assertEqual(invoice.variance, Decimal("0.00"))
        self.assertIsNotNone(invoice.expense)

    def test_an_invoice_due_date_cannot_precede_its_invoice_date(self):
        invoice = SupplierInvoice(
            supplier=self.supplier,
            number="INV-004",
            invoice_date=date(2026, 2, 10),
            due_date=date(2026, 2, 1),
            amount=Decimal("10.00"),
        )
        with self.assertRaises(ValidationError):
            invoice.clean()

    def test_the_requisition_page_compares_quotations_and_awards_one(self):
        requisition = self._requisition()
        quotation = Quotation.objects.create(
            institution=self.institution,
            requisition=requisition,
            supplier=self.supplier,
            amount=Decimal("75.00"),
        )
        self.client.force_login(self.admin)
        page = self.client.get(reverse("operations:requisition_detail", args=[requisition.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Stationery Ltd")
        self.assertContains(page, quotation.number)
        response = self.client.post(
            reverse("operations:quotation_award", args=[quotation.pk])
        )
        self.assertEqual(response.status_code, 302)
        quotation.refresh_from_db()
        self.assertEqual(quotation.status, Quotation.Status.AWARDED)
        self.assertTrue(PurchaseOrder.objects.filter(requisition=requisition).exists())

    def test_the_supplier_invoice_page_matches_from_the_register(self):
        order = self._order("20.00")
        invoice = SupplierInvoice.objects.create(
            institution=self.institution,
            supplier=self.supplier,
            order=order,
            number="INV-005",
            amount=Decimal("20.00"),
        )
        self.client.force_login(self.admin)
        page = self.client.get(
            reverse("operations:supplier_invoice_match", args=[invoice.pk])
        )
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Expected total")
        response = self.client.post(
            reverse("operations:supplier_invoice_match", args=[invoice.pk])
        )
        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, SupplierInvoice.Status.MATCHED)

    def test_a_user_without_procurement_access_cannot_open_the_follow_up_pages(self):
        requisition = self._requisition()
        invoice = SupplierInvoice.objects.create(
            institution=self.institution,
            supplier=self.supplier,
            number="INV-006",
            amount=Decimal("5.00"),
        )
        user = UserFactory(
            username="procurement_outsider", role="lecturer", institution=self.institution
        )
        self.client.force_login(user)
        self.assertEqual(
            self.client.get(
                reverse("operations:requisition_detail", args=[requisition.pk])
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                reverse("operations:supplier_invoice_match", args=[invoice.pk])
            ).status_code,
            403,
        )
