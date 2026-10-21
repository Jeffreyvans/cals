from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum

class Invoice(models.Model):
    student = models.ForeignKey("academics.Student", on_delete=models.CASCADE, related_name="invoices")
    description = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    due_date = models.DateField()
    created = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="invoices_created")
    @property
    def paid(self): return self.payments.aggregate(t=Sum("amount"))["t"] or Decimal("0")
    @property
    def waived(self): return self.waivers.aggregate(t=Sum("amount"))["t"] or Decimal("0")
    @property
    def balance(self): return self.amount - self.paid - self.waived
    def save(self, *a, **kw):
        adding = self._state.adding
        super().save(*a, **kw)
        if adding:
            MoneyLog.objects.create(action=MoneyLog.Action.INVOICE,
                amount=self.amount, student=self.student, invoice=self,
                actor=self.created_by, description=self.description)
    def __str__(self): return f"{self.description} – {self.student.name}"

class Payment(models.Model):
    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        MOBILE = "mobile", "Mobile money"
        BANK = "bank", "Bank transfer"
        CARD = "card", "Card"
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=10, choices=Method.choices, default=Method.CASH)
    reference = models.CharField(max_length=80, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="payments_received")
    paid_on = models.DateTimeField(auto_now_add=True)
    receipt_no = models.CharField(max_length=20, unique=True, blank=True)

    def available_for_payment(self):
        """Invoice balance left after ignoring this payment itself."""
        if not self.invoice_id:
            return None
        if not self.pk:
            return self.invoice.balance
        old = Payment.objects.filter(pk=self.pk).values_list("amount", flat=True).first() or Decimal("0")
        return self.invoice.amount - (self.invoice.paid - old) - self.invoice.waived

    def clean(self):
        super().clean()
        if self.amount is None:
            return
        if self.amount <= 0:
            raise ValidationError({"amount": "Amount must be above zero."})
        available = self.available_for_payment()
        if available is not None and self.amount > available:
            raise ValidationError({"amount": f"Payment exceeds the invoice balance of {available}."})

    def save(self, *a, **kw):
        adding = self._state.adding
        if adding:
            self.full_clean(exclude=["receipt_no"])
        super().save(*a, **kw)
        if not self.receipt_no:
            self.receipt_no = f"RC{self.paid_on:%y%m}{self.pk:06d}"
            super().save(update_fields=["receipt_no"])
        if adding:
            MoneyLog.objects.create(action=MoneyLog.Action.PAYMENT,
                amount=self.amount, student=self.invoice.student,
                invoice=self.invoice, actor=self.received_by,
                reference=self.receipt_no,
                description=self.invoice.description)

    @property
    def received_by_name(self):
        if not self.received_by_id:
            return "EduSphere office"
        return self.received_by.get_full_name() or self.received_by.username

    def __str__(self): return f"{self.receipt_no} {self.amount}"


class Waiver(models.Model):
    """A credit note: money the office chooses not to collect from an invoice."""
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="waivers")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.CharField(max_length=200,
                              help_text="Why the money is being forgiven.")
    note_no = models.CharField(max_length=20, unique=True, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="waivers_recorded")
    created = models.DateTimeField(auto_now_add=True)

    def available_for_waiver(self):
        """Invoice balance left after ignoring this waiver itself."""
        if not self.invoice_id:
            return None
        if not self.pk:
            return self.invoice.balance
        old = Waiver.objects.filter(pk=self.pk).values_list("amount", flat=True).first() or Decimal("0")
        return self.invoice.amount - self.invoice.paid - (self.invoice.waived - old)

    def clean(self):
        super().clean()
        if self.amount is None:
            return
        if self.amount <= 0:
            raise ValidationError({"amount": "Amount must be above zero."})
        available = self.available_for_waiver()
        if available is not None and self.amount > available:
            raise ValidationError({"amount":
                f"Amount exceeds what is still owed on this invoice ({available})."})

    def save(self, *a, **kw):
        adding = self._state.adding
        if adding:
            self.full_clean(exclude=["note_no"])
        super().save(*a, **kw)
        if not self.note_no:
            self.note_no = f"CN{self.created:%y%m}{self.pk:06d}"
            super().save(update_fields=["note_no"])
        if adding:
            MoneyLog.objects.create(action=MoneyLog.Action.WAIVER,
                amount=self.amount, student=self.invoice.student,
                invoice=self.invoice, actor=self.recorded_by,
                reference=self.note_no, description=self.reason)

    @property
    def recorded_by_name(self):
        if not self.recorded_by_id:
            return "EduSphere office"
        return self.recorded_by.get_full_name() or self.recorded_by.username

    def __str__(self): return f"{self.note_no} {self.amount}"


class MoneyLog(models.Model):
    """Append-only record of every invoice, payment and credit note."""
    class Action(models.TextChoices):
        INVOICE = "invoice", "Invoice issued"
        PAYMENT = "payment", "Payment received"
        WAIVER = "waiver", "Credit note issued"
        ADJUST = "adjust", "Record edited"
        REMOVE = "remove", "Record deleted"
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="money_entries")
    action = models.CharField(max_length=10, choices=Action.choices)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    student = models.ForeignKey("academics.Student", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="money_entries")
    invoice = models.ForeignKey(Invoice, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="money_entries")
    reference = models.CharField(max_length=20, blank=True,
        help_text="Receipt or credit note number.")
    description = models.CharField(max_length=200, blank=True,
        help_text="What the money was for.")
    created = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created", "-pk"]

    @property
    def actor_name(self):
        if not self.actor_id:
            return "EduSphere office"
        return self.actor.get_full_name() or self.actor.username

    def __str__(self):
        return (f"{self.created:%Y-%m-%d %H:%M} {self.get_action_display()} "
                f"{self.amount}")
