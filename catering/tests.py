from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from academics.factories import StudentFactory
from catering.models import (
    StockItem,
    StockPurchase,
    Wallet,
    WalletTransaction,
)
from finance.models import Expense


class CateringTests(TestCase):
    def test_a_top_up_and_a_purchase_change_the_wallet_balance(self):
        student = StudentFactory(status="active")
        wallet = Wallet.objects.create(student=student, balance=Decimal("10.00"))
        WalletTransaction.objects.create(
            wallet=wallet, kind=WalletTransaction.Kind.TOP_UP, amount=Decimal("5.00")
        )
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, Decimal("15.00"))
        WalletTransaction.objects.create(
            wallet=wallet, kind=WalletTransaction.Kind.PURCHASE, amount=Decimal("4.00")
        )
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, Decimal("11.00"))

    def test_a_purchase_cannot_go_below_zero(self):
        student = StudentFactory(status="active")
        wallet = Wallet.objects.create(student=student, balance=Decimal("2.00"))
        with self.assertRaises(ValidationError):
            WalletTransaction.objects.create(
                wallet=wallet,
                kind=WalletTransaction.Kind.PURCHASE,
                amount=Decimal("5.00"),
            )


class CateringStockTests(TestCase):
    def setUp(self):
        self.item = StockItem.objects.create(
            name="Mealie meal",
            category=StockItem.Category.FOOD,
            unit="bag",
            quantity=Decimal("2"),
            reorder_level=Decimal("5"),
            unit_cost=Decimal("8.00"),
        )

    def test_a_purchase_adds_to_stock_and_posts_an_expense(self):
        purchase = StockPurchase.objects.create(
            item=self.item,
            quantity=Decimal("10"),
            unit_cost=Decimal("8.00"),
            supplier="Millers",
        )
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, Decimal("12.00"))
        self.assertEqual(purchase.total_cost, Decimal("80.00"))
        expense = Expense.objects.get(pk=purchase.expense_id)
        self.assertEqual(expense.amount, Decimal("80.00"))
        self.assertEqual(expense.category, "Catering stock")
        self.assertIsNotNone(expense.journal_entry_id)

    def test_an_item_at_or_below_its_reorder_level_is_flagged(self):
        self.assertTrue(self.item.needs_reorder)
        StockPurchase.objects.create(
            item=self.item, quantity=Decimal("10"), unit_cost=Decimal("8.00")
        )
        self.item.refresh_from_db()
        self.assertFalse(self.item.needs_reorder)
