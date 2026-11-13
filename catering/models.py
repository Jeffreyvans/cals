"""Catering menus, meal bookings, wallets, tuck-shop sales and stock."""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from core.models import TenantModel


class Meal(TenantModel):
    class Kind(models.TextChoices):
        BREAKFAST = "breakfast", "Breakfast"
        LUNCH = "lunch", "Lunch"
        SUPPER = "supper", "Supper"
        SNACK = "snack", "Snack"

    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.LUNCH)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["kind", "name"]

    def __str__(self):
        return f"{self.name} ({self.get_kind_display()})"


class Menu(TenantModel):
    date = models.DateField(default=timezone.localdate)
    meal = models.ForeignKey(Meal, on_delete=models.CASCADE, related_name="menus")
    items = models.TextField(help_text="What is served on this day.")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-date", "meal__kind"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "date", "meal"], name="uniq_menu_meal_date"
            )
        ]

    def __str__(self):
        return f"{self.date} {self.meal}"


class MealBooking(TenantModel):
    class Status(models.TextChoices):
        BOOKED = "booked", "Booked"
        SERVED = "served", "Served"
        MISSED = "missed", "Missed"
        CANCELLED = "cancelled", "Cancelled"

    student = models.ForeignKey(
        "academics.Student", on_delete=models.CASCADE, related_name="meal_bookings"
    )
    menu = models.ForeignKey(Menu, on_delete=models.CASCADE, related_name="bookings")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.BOOKED)
    booked_on = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["menu__date", "student__user__last_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "student", "menu"], name="uniq_meal_booking"
            )
        ]

    def __str__(self):
        return f"{self.student} - {self.menu}"


class Wallet(TenantModel):
    student = models.OneToOneField(
        "academics.Student", on_delete=models.CASCADE, related_name="catering_wallet"
    )
    balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal("0"))],
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["student__user__last_name"]

    def __str__(self):
        return f"Wallet for {self.student}"


class WalletTransaction(TenantModel):
    class Kind(models.TextChoices):
        TOP_UP = "top_up", "Top up"
        PURCHASE = "purchase", "Purchase"
        REFUND = "refund", "Refund"
        ADJUSTMENT = "adjustment", "Adjustment"

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="transactions")
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.PURCHASE)
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    description = models.CharField(max_length=200, blank=True)
    receipt_invoice = models.ForeignKey(
        "finance.Invoice",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="wallet_transactions",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="wallet_transactions_created",
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created", "-pk"]

    def clean(self):
        super().clean()
        if (
            self.wallet_id
            and self.kind == self.Kind.PURCHASE
            and self.amount
            and self.amount > self.wallet.balance
        ):
            raise ValidationError({"amount": "The wallet balance is too low."})

    def save(self, *a, **kw):
        adding = self._state.adding
        self.full_clean()
        super().save(*a, **kw)
        if not adding:
            return
        if self.kind in (self.Kind.TOP_UP, self.Kind.REFUND):
            Wallet.all_objects.filter(pk=self.wallet_id).update(
                balance=models.F("balance") + self.amount
            )
        else:
            Wallet.all_objects.filter(pk=self.wallet_id).update(
                balance=models.F("balance") - self.amount
            )

    def __str__(self):
        return f"{self.get_kind_display()} {self.amount} - {self.wallet}"


class StockItem(TenantModel):
    """A kitchen or tuck-shop consumable tracked by quantity on hand."""

    institution = models.ForeignKey(
        "core.Institution",
        on_delete=models.CASCADE,
        related_name="catering_stockitem_records",
    )

    class Category(models.TextChoices):
        FOOD = "food", "Food"
        DRINK = "drink", "Drink"
        CLEANING = "cleaning", "Cleaning"
        OTHER = "other", "Other"

    name = models.CharField(max_length=120)
    category = models.CharField(
        max_length=20, choices=Category.choices, default=Category.FOOD
    )
    unit = models.CharField(max_length=20, default="unit")
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal("0"))],
    )
    reorder_level = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal("0"))],
    )
    unit_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal("0"))],
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "name"], name="uniq_stock_item_name"
            )
        ]

    @property
    def needs_reorder(self):
        return self.quantity <= self.reorder_level

    def __str__(self):
        return f"{self.name} ({self.quantity} {self.unit})"


class StockPurchase(TenantModel):
    """A delivery into the store; also posts the cost to the ledger."""

    item = models.ForeignKey(
        StockItem, on_delete=models.CASCADE, related_name="purchases"
    )
    date = models.DateField(default=timezone.localdate)
    quantity = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    unit_cost = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0"))]
    )
    supplier = models.CharField(max_length=120, blank=True)
    reference = models.CharField(max_length=80, blank=True)
    notes = models.TextField(blank=True)
    expense = models.ForeignKey(
        "finance.Expense",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="stock_purchases",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="stock_purchases_created",
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-pk"]

    @property
    def total_cost(self):
        return (self.quantity or Decimal("0")) * (self.unit_cost or Decimal("0"))

    def save(self, *a, **kw):
        adding = self._state.adding
        super().save(*a, **kw)
        if not adding:
            return
        StockItem.all_objects.filter(pk=self.item_id).update(
            quantity=models.F("quantity") + self.quantity
        )
        if self.total_cost > 0:
            from finance.models import Expense

            expense = Expense.objects.create(
                institution=self.institution,
                supplier=None,
                description=f"Stock purchase: {self.item.name}"[:200],
                category="Catering stock",
                amount=self.total_cost,
                date=self.date,
                reference=self.reference or f"STK{self.pk}",
                created_by=self.created_by,
            )
            StockPurchase.all_objects.filter(pk=self.pk).update(expense=expense)
            self.expense = expense

    def __str__(self):
        return f"{self.item} +{self.quantity} on {self.date}"
