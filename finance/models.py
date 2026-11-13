import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from core.models import TenantModel

# The standard VAT rate used when an institution is registered but has not
# entered a dated TaxRate row yet. It is only ever applied to standard-rated
# items of a VAT-registered institution.
DEFAULT_VAT_RATE = Decimal("15.50")


class TaxCategory(models.TextChoices):
    EXEMPT = "exempt", "Exempt"
    STANDARD = "standard", "Standard rated"
    ZERO = "zero", "Zero rated"


class DocumentType(models.TextChoices):
    INVOICE = "invoice", "Invoice"
    RECEIPT = "receipt", "Receipt"
    CREDIT_NOTE = "credit_note", "Credit note"
    DEBIT_NOTE = "debit_note", "Debit note"


DOCUMENT_PREFIXES = {
    DocumentType.INVOICE: "INV",
    DocumentType.RECEIPT: "RC",
    DocumentType.CREDIT_NOTE: "CN",
    DocumentType.DEBIT_NOTE: "DN",
}


class ImmutableFinancialDocument(models.Model):
    """An issued financial document.

    Once written, the amounts on an issued document never change: a mistake is
    corrected with a credit note, a debit note or a reversing journal entry.
    A second ``save`` that would change a protected field, or any ``delete``,
    is refused. Internal writes (such as allocating the document number) happen
    before the first insert, so they are not affected.
    """

    PROTECTED_FIELDS = ()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            changed = self._changed_protected_fields()
            if changed:
                fields = ", ".join(sorted(changed))
                raise ValidationError(
                    "An issued financial document cannot be changed "
                    f"({fields}). Reverse it with a credit or debit note instead.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "Financial documents are kept for six years and cannot be deleted. "
            "Post a reversing entry instead.")

    def _changed_protected_fields(self):
        if not self.pk or not self.PROTECTED_FIELDS:
            return {}
        stored = type(self).all_objects.filter(pk=self.pk).values(
            *self.PROTECTED_FIELDS).first()
        if stored is None:
            return {}
        return {
            field: (stored[field], getattr(self, field))
            for field in self.PROTECTED_FIELDS
            if stored[field] != getattr(self, field)
        }


class FeeStructure(TenantModel):
    """A price list: what a student is charged and how much for each item.

    One structure per programme, class or year. The items are the lines of
    the bill, so a school can split tuition into tuition, books and a levy.
    """

    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    academic_year = models.ForeignKey(
        "academics.AcademicYear", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="fee_structures")
    programme = models.ForeignKey(
        "academics.Programme", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="fee_structures")
    school_class = models.ForeignKey(
        "academics.SchoolClass", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="fee_structures")
    term = models.ForeignKey(
        "academics.Term", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="fee_structures")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["institution", "code"], name="uniq_fee_structure_code")
        ]

    def __str__(self):
        return f"{self.name} ({self.code})"

    @property
    def total(self):
        return sum((item.amount for item in self.items.all()), Decimal("0"))


class FeeItem(TenantModel):
    """One line of a fee structure, for example 'Tuition' at 900.00."""

    class Kind(models.TextChoices):
        TUITION = "tuition", "Tuition"
        BOARDING = "boarding", "Boarding"
        TRANSPORT = "transport", "Transport"
        EXAM = "exam", "Examination fee"
        LEVY = "levy", "Levy"
        OTHER = "other", "Other"

    structure = models.ForeignKey(FeeStructure, on_delete=models.CASCADE, related_name="items")
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.TUITION)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    tax_category = models.CharField(max_length=20, choices=TaxCategory.choices,
                                    default=TaxCategory.EXEMPT)
    is_mandatory = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["structure", "name"], name="uniq_fee_item_name")
        ]

    def __str__(self):
        return f"{self.name} {self.amount}"


class TaxRate(TenantModel):
    """A dated VAT rate. The rate on a document is the one in force that day."""

    name = models.CharField(max_length=80)
    category = models.CharField(max_length=20, choices=TaxCategory.choices)
    rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    effective_from = models.DateField(default=timezone.localdate)
    effective_to = models.DateField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["category", "-effective_from"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "category", "effective_from"],
                name="uniq_tax_rate_period")
        ]

    def __str__(self):
        return f"{self.get_category_display()} {self.rate}% from {self.effective_from}"

    @classmethod
    def rate_on(cls, institution, category, day):
        row = cls.all_objects.filter(
            institution=institution, category=category,
            effective_from__lte=day).filter(
                models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gte=day)).order_by("-effective_from").first()
        return row.rate if row is not None else None


class ExchangeRate(TenantModel):
    """A dated rate used to convert a foreign amount into the base currency."""

    currency = models.CharField(max_length=10)
    rate = models.DecimalField(max_digits=14, decimal_places=6, default=Decimal("1"))
    effective_from = models.DateField(default=timezone.localdate)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["currency", "-effective_from"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "currency", "effective_from"],
                name="uniq_exchange_rate_period")
        ]

    def __str__(self):
        return f"{self.currency} @ {self.rate} from {self.effective_from}"

    @classmethod
    def rate_on(cls, institution, currency, day):
        row = cls.all_objects.filter(
            institution=institution, currency=currency,
            effective_from__lte=day).order_by("-effective_from").first()
        return row.rate if row is not None else None


class DocumentSequence(TenantModel):
    """The last number used for a document type in a financial year.

    One row per institution, document type and year. The number is advanced
    with an atomic database update inside a lock, so two clerks issuing at the
    same moment never share a number and none is skipped.
    """

    document_type = models.CharField(max_length=20, choices=DocumentType.choices)
    financial_year = models.CharField(max_length=10)
    last_number = models.PositiveIntegerField(default=0)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["document_type", "financial_year"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "document_type", "financial_year"],
                name="uniq_document_sequence")
        ]

    def __str__(self):
        return f"{self.document_type} {self.financial_year}: {self.last_number}"


class Invoice(ImmutableFinancialDocument, TenantModel):
    student = models.ForeignKey("academics.Student", on_delete=models.CASCADE, related_name="invoices")
    description = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    due_date = models.DateField()
    term = models.ForeignKey(
        "academics.Term", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="invoices")
    structure = models.ForeignKey(
        FeeStructure, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="invoices")
    fee_item = models.ForeignKey(
        FeeItem, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="invoices")
    # The gap-free document number allocated per institution and year.
    number = models.CharField(max_length=20, blank=True)
    # Tax. The amount above is VAT-inclusive, so tax_amount is the VAT inside it.
    tax_category = models.CharField(max_length=20, choices=TaxCategory.choices,
                                    default=TaxCategory.EXEMPT)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    currency = models.CharField(max_length=10, blank=True)
    exchange_rate = models.DecimalField(max_digits=14, decimal_places=6,
                                        default=Decimal("1"))
    base_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="invoices_created")

    PROTECTED_FIELDS = ("student_id", "description", "amount", "due_date",
                        "tax_category", "tax_rate", "tax_amount", "currency",
                        "exchange_rate", "base_amount")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["institution", "number"],
                                    name="uniq_invoice_number")
        ]

    def __str__(self): return f"{self.description} – {self.student.name}"

    def save(self, *a, **kw):
        adding = self._state.adding
        if adding:
            from .services import next_document_number

            if not self.number:
                self.number = next_document_number(
                    self.institution, DocumentType.INVOICE)
            self._apply_tax()
            if not self.currency:
                self.currency = (getattr(self.institution, "base_currency", "")
                                 or getattr(self.institution, "currency", "") or "USD")
            if self.base_amount is None:
                self.base_amount = (self.amount or Decimal("0")) * (
                    self.exchange_rate or Decimal("1"))
        super().save(*a, **kw)
        if adding:
            MoneyLog.objects.create(action=MoneyLog.Action.INVOICE,
                amount=self.amount, student=self.student, invoice=self,
                actor=self.created_by, description=self.description)
            from .services import post_invoice
            post_invoice(self)
            from .signals import financial_document_issued
            financial_document_issued.send(
                sender=type(self), instance=self, document_type=DocumentType.INVOICE)

    @property
    def paid(self): return self.payments.aggregate(t=Sum("amount"))["t"] or Decimal("0")
    @property
    def refunded(self):
        return Refund.objects.filter(
            payment__invoice=self, status=Refund.Status.APPROVED).aggregate(
                t=Sum("amount"))["t"] or Decimal("0")
    @property
    def waived(self): return self.waivers.aggregate(t=Sum("amount"))["t"] or Decimal("0")
    @property
    def debited(self): return self.debit_notes.aggregate(t=Sum("amount"))["t"] or Decimal("0")
    @property
    def discounted(self):
        return self.discounts.aggregate(t=Sum("amount"))["t"] or Decimal("0")
    @property
    def credit_applied(self):
        return self.credit_applications.aggregate(t=Sum("amount"))["t"] or Decimal("0")
    @property
    def net_amount(self):
        """The amount excluding the VAT it carries."""
        return self.amount - self.tax_amount
    @property
    def balance(self):
        return (self.amount + self.debited - (self.paid - self.refunded) - self.waived
                - self.discounted - self.credit_applied)

    def _apply_tax(self):
        from .services import tax_rate_in_force

        institution = self.institution
        if self.fee_item_id and self.tax_category == TaxCategory.EXEMPT:
            self.tax_category = self.fee_item.tax_category
        category = self.tax_category or TaxCategory.EXEMPT
        rate = tax_rate_in_force(institution, category, timezone.localdate())
        self.tax_category = category
        self.tax_rate = rate
        gross = Decimal(self.amount or 0)
        if rate and gross:
            self.tax_amount = (gross - gross / (Decimal("1") + rate / Decimal("100"))
                               ).quantize(Decimal("0.01"))
        else:
            self.tax_amount = Decimal("0")

class Payment(ImmutableFinancialDocument, TenantModel):
    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        MOBILE = "mobile", "Mobile money"
        BANK = "bank", "Bank transfer"
        CARD = "card", "Card"

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=10, choices=Method.choices, default=Method.CASH)
    reference = models.CharField(max_length=80, blank=True)
    payer_name = models.CharField(max_length=120, blank=True)
    currency = models.CharField(max_length=10, blank=True)
    exchange_rate = models.DecimalField(max_digits=14, decimal_places=6, default=Decimal("1"),
                                         blank=True)
    base_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    verification_token = models.UUIDField(unique=True, editable=False,
                                          null=True, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="payments_received")
    paid_on = models.DateTimeField(auto_now_add=True)
    receipt_no = models.CharField(max_length=20, blank=True)

    PROTECTED_FIELDS = ("invoice_id", "amount", "method", "reference",
                        "payer_name", "currency", "exchange_rate",
                        "base_amount", "receipt_no")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["institution", "receipt_no"], name="uniq_receipt_no")
        ]

    def __str__(self): return f"{self.receipt_no} {self.amount}"

    def save(self, *a, **kw):
        adding = self._state.adding
        if adding:
            self.full_clean(exclude=["receipt_no"])
        if not self.verification_token:
            self.verification_token = uuid.uuid4()
        if not self.currency:
            self.currency = getattr(self.invoice.student.institution, "currency", "$")
        if self.base_amount is None:
            self.base_amount = (self.amount or Decimal("0")) * (self.exchange_rate or Decimal("1"))
        if not self.receipt_no:
            from .services import next_document_number

            self.receipt_no = next_document_number(
                self.institution, DocumentType.RECEIPT)
        super().save(*a, **kw)
        if adding:
            MoneyLog.objects.create(action=MoneyLog.Action.PAYMENT,
                amount=self.amount, student=self.invoice.student,
                invoice=self.invoice, actor=self.received_by,
                reference=self.receipt_no,
                description=self.invoice.description)
            from .services import post_payment
            post_payment(self)
            from .signals import financial_document_issued
            financial_document_issued.send(
                sender=type(self), instance=self, document_type=DocumentType.RECEIPT)

    @property
    def received_by_name(self):
        if not self.received_by_id:
            return "EduSphere office"
        return self.received_by.get_full_name() or self.received_by.username

    @property
    def net_amount(self):
        refunded = self.refunds.filter(status=Refund.Status.APPROVED).aggregate(
            t=Sum("amount"))["t"] or Decimal("0")
        return self.amount - refunded

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


class Waiver(ImmutableFinancialDocument, TenantModel):
    """A credit note: money the office chooses not to collect from an invoice."""
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="waivers")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.CharField(max_length=200,
                              help_text="Why the money is being forgiven.")
    note_no = models.CharField(max_length=20, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="waivers_recorded")
    created = models.DateTimeField(auto_now_add=True)

    PROTECTED_FIELDS = ("invoice_id", "amount", "reason", "note_no")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["institution", "note_no"], name="uniq_credit_note_no")
        ]

    def __str__(self): return f"{self.note_no} {self.amount}"

    def save(self, *a, **kw):
        adding = self._state.adding
        if adding:
            self.full_clean(exclude=["note_no"])
        if not self.note_no:
            from .services import next_document_number

            self.note_no = next_document_number(
                self.institution, DocumentType.CREDIT_NOTE)
        super().save(*a, **kw)
        if adding:
            MoneyLog.objects.create(action=MoneyLog.Action.WAIVER,
                amount=self.amount, student=self.invoice.student,
                invoice=self.invoice, actor=self.recorded_by,
                reference=self.note_no, description=self.reason)
            from .services import post_waiver
            post_waiver(self)
            from .signals import financial_document_issued
            financial_document_issued.send(
                sender=type(self), instance=self, document_type=DocumentType.CREDIT_NOTE)

    @property
    def recorded_by_name(self):
        if not self.recorded_by_id:
            return "EduSphere office"
        return self.recorded_by.get_full_name() or self.recorded_by.username

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


class DebitNote(ImmutableFinancialDocument, TenantModel):
    """A debit note: an extra charge added to an issued invoice.

    Where a credit note forgives money, a debit note adds it. Both are the only
    permitted way to correct an invoice once it has been issued.
    """

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE,
                                related_name="debit_notes")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.CharField(max_length=200,
                              help_text="Why the extra charge is being raised.")
    note_no = models.CharField(max_length=20, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="debit_notes_recorded")
    created = models.DateTimeField(auto_now_add=True)

    PROTECTED_FIELDS = ("invoice_id", "amount", "reason", "note_no")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["institution", "note_no"],
                                    name="uniq_debit_note_no")
        ]

    def __str__(self): return f"{self.note_no} {self.amount}"

    def save(self, *a, **kw):
        adding = self._state.adding
        if adding:
            self.full_clean(exclude=["note_no"])
        if not self.note_no:
            from .services import next_document_number

            self.note_no = next_document_number(
                self.institution, DocumentType.DEBIT_NOTE)
        super().save(*a, **kw)
        if adding:
            MoneyLog.objects.create(action=MoneyLog.Action.DEBIT_NOTE,
                amount=self.amount, student=self.invoice.student,
                invoice=self.invoice, actor=self.recorded_by,
                reference=self.note_no, description=self.reason)
            from .services import post_debit_note
            post_debit_note(self)
            from .signals import financial_document_issued
            financial_document_issued.send(
                sender=type(self), instance=self, document_type=DocumentType.DEBIT_NOTE)

    @property
    def recorded_by_name(self):
        if not self.recorded_by_id:
            return "EduSphere office"
        return self.recorded_by.get_full_name() or self.recorded_by.username

    def clean(self):
        super().clean()
        if self.amount is None:
            return
        if self.amount <= 0:
            raise ValidationError({"amount": "Amount must be above zero."})


class MoneyLog(TenantModel):
    """Append-only record of every invoice, payment and credit note."""
    class Action(models.TextChoices):
        INVOICE = "invoice", "Invoice issued"
        PAYMENT = "payment", "Payment received"
        WAIVER = "waiver", "Credit note issued"
        DEBIT_NOTE = "debit_note", "Debit note issued"
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

# ---------- chart of accounts and the double-entry ledger ----------


class Account(TenantModel):
    """One line of the chart of accounts."""

    class Type(models.TextChoices):
        ASSET = "asset", "Asset"
        LIABILITY = "liability", "Liability"
        EQUITY = "equity", "Equity"
        INCOME = "income", "Income"
        EXPENSE = "expense", "Expense"

    code = models.CharField(max_length=20)
    name = models.CharField(max_length=120)
    type = models.CharField(max_length=20, choices=Type.choices)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(fields=["institution", "code"],
                                    name="uniq_account_code")
        ]

    def __str__(self):
        return f"{self.code} {self.name}"

    @property
    def normal_side(self):
        return "credit" if self.type in (self.Type.LIABILITY, self.Type.EQUITY,
                                         self.Type.INCOME) else "debit"


class FinancialYear(TenantModel):
    """A year that can be closed so its posted entries stop changing."""

    name = models.CharField(max_length=40)
    start_date = models.DateField()
    end_date = models.DateField()
    is_closed = models.BooleanField(default=False)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL,
                                  related_name="financial_years_closed")

    class Meta:
        ordering = ["-start_date"]
        constraints = [
            models.UniqueConstraint(fields=["institution", "name"],
                                    name="uniq_financial_year_name")
        ]

    def __str__(self):
        return self.name

    def contains(self, day):
        return self.start_date <= day <= self.end_date


class JournalEntry(TenantModel):
    """A balanced set of debit and credit lines."""

    number = models.CharField(max_length=20, blank=True)
    date = models.DateField(default=timezone.localdate)
    description = models.CharField(max_length=200)
    reference = models.CharField(max_length=80, blank=True)
    source_type = models.CharField(max_length=60, blank=True)
    source_id = models.CharField(max_length=40, blank=True)
    financial_year = models.ForeignKey(FinancialYear, null=True, blank=True,
                                       on_delete=models.SET_NULL,
                                       related_name="entries")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name="journal_entries")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["institution", "number"],
                                    name="uniq_journal_number")
        ]

    def save(self, *a, **kw):
        super().save(*a, **kw)
        if not self.number:
            self.number = f"JE{self.date:%y%m}{self.pk:06d}"
            super().save(update_fields=["number"])

    @property
    def total_debit(self):
        return self.lines.aggregate(t=Sum("debit"))["t"] or Decimal("0")

    @property
    def total_credit(self):
        return self.lines.aggregate(t=Sum("credit"))["t"] or Decimal("0")

    @property
    def is_balanced(self):
        return self.total_debit == self.total_credit

    def __str__(self):
        return f"{self.number} {self.description}"


class JournalLine(TenantModel):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE,
                              related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT,
                                related_name="journal_lines")
    debit = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    credit = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    currency = models.CharField(max_length=10, blank=True)
    exchange_rate = models.DecimalField(max_digits=14, decimal_places=6,
                                        default=Decimal("1"))
    base_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["pk"]

    def clean(self):
        super().clean()
        if (self.debit or 0) < 0 or (self.credit or 0) < 0:
            raise ValidationError("Debit and credit cannot be negative.")
        if (self.debit or 0) > 0 and (self.credit or 0) > 0:
            raise ValidationError("A line is either a debit or a credit, not both.")
        if not (self.debit or 0) and not (self.credit or 0):
            raise ValidationError("A line needs a debit or a credit.")

    def save(self, *a, **kw):
        self.full_clean()
        if self.base_amount is None:
            self.base_amount = ((self.debit or Decimal("0"))
                                + (self.credit or Decimal("0"))) * (
                                    self.exchange_rate or Decimal("1"))
        super().save(*a, **kw)

    def __str__(self):
        return f"{self.account.code} D{self.debit} C{self.credit}"


# ---------- sponsors, discounts and instalments ----------


class Sponsor(TenantModel):
    class Type(models.TextChoices):
        COMPANY = "company", "Company"
        NGO = "ngo", "NGO"
        GOVERNMENT = "government", "Government"
        INDIVIDUAL = "individual", "Individual"

    name = models.CharField(max_length=160)
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.COMPANY)
    contact_name = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Sponsorship(TenantModel):
    """How much of a student's bill a sponsor carries."""

    sponsor = models.ForeignKey(Sponsor, on_delete=models.CASCADE,
                                related_name="sponsorships")
    student = models.ForeignKey("academics.Student", on_delete=models.CASCADE,
                                related_name="sponsorships")
    percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True,
                                     blank=True, validators=[MinValueValidator(0)])
    fixed_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True,
                                       blank=True, validators=[MinValueValidator(0)])
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    reference = models.CharField(max_length=80, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sponsor__name", "student__user__last_name"]

    def __str__(self):
        return f"{self.sponsor} - {self.student}"

    def covers(self, invoice):
        if not self.is_active:
            return Decimal("0")
        day = invoice.due_date
        if self.valid_from and day < self.valid_from:
            return Decimal("0")
        if self.valid_to and day > self.valid_to:
            return Decimal("0")
        if self.percentage is not None:
            return (invoice.amount * self.percentage / Decimal("100")).quantize(
                Decimal("0.01"))
        return min(self.fixed_amount or Decimal("0"), invoice.amount)


class Discount(TenantModel):
    """A discount, bursary, scholarship or staff-child concession."""

    class Category(models.TextChoices):
        DISCOUNT = "discount", "Discount"
        BURSARY = "bursary", "Bursary"
        SCHOLARSHIP = "scholarship", "Scholarship"
        STAFF_CHILD = "staff_child", "Staff-child discount"

    class Kind(models.TextChoices):
        PERCENTAGE = "percentage", "Percentage"
        FIXED = "fixed", "Fixed amount"

    student = models.ForeignKey("academics.Student", on_delete=models.CASCADE,
                                related_name="discounts")
    invoice = models.ForeignKey(Invoice, null=True, blank=True,
                                on_delete=models.CASCADE, related_name="discounts")
    fee_item = models.ForeignKey(FeeItem, null=True, blank=True,
                                 on_delete=models.SET_NULL,
                                 related_name="discounts")
    sponsor = models.ForeignKey(Sponsor, null=True, blank=True,
                                on_delete=models.SET_NULL,
                                related_name="discounts")
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=20, choices=Category.choices,
                                default=Category.DISCOUNT)
    kind = models.CharField(max_length=20, choices=Kind.choices,
                            default=Kind.PERCENTAGE)
    value = models.DecimalField(max_digits=10, decimal_places=2,
                                validators=[MinValueValidator(0)])
    amount = models.DecimalField(max_digits=10, decimal_places=2,
                                 default=Decimal("0"))
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    reason = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name="discounts_created")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created", "-pk"]

    def __str__(self):
        return f"{self.name} for {self.student}"

    def compute_amount(self, invoice=None):
        base = (invoice or self.invoice)
        if base is None:
            return self.amount or Decimal("0")
        if self.kind == self.Kind.PERCENTAGE:
            return (base.amount * self.value / Decimal("100")).quantize(Decimal("0.01"))
        return min(self.value, base.amount)

    def save(self, *a, **kw):
        if self.invoice_id and not self.amount:
            self.amount = self.compute_amount(self.invoice)
        super().save(*a, **kw)


class InstalmentPlan(TenantModel):
    """An agreed schedule of payments for a student's term."""

    student = models.ForeignKey("academics.Student", on_delete=models.CASCADE,
                                related_name="instalment_plans")
    term = models.ForeignKey("academics.Term", null=True, blank=True,
                             on_delete=models.SET_NULL,
                             related_name="instalment_plans")
    name = models.CharField(max_length=120, default="Instalment plan")
    total_amount = models.DecimalField(max_digits=12, decimal_places=2,
                                       default=Decimal("0"))
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name="instalment_plans_created")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created", "-pk"]

    def __str__(self):
        return f"{self.name} - {self.student}"

    @property
    def scheduled_total(self):
        return self.instalments.aggregate(t=Sum("amount"))["t"] or Decimal("0")

    @property
    def paid_total(self):
        return self.instalments.aggregate(t=Sum("paid_amount"))["t"] or Decimal("0")

    @property
    def balance(self):
        return self.scheduled_total - self.paid_total


class Instalment(TenantModel):
    plan = models.ForeignKey(InstalmentPlan, on_delete=models.CASCADE,
                             related_name="instalments")
    due_date = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2,
                                      default=Decimal("0"))

    class Meta:
        ordering = ["due_date", "pk"]

    def __str__(self):
        return f"{self.plan} {self.due_date} {self.amount}"

    @property
    def balance(self):
        return self.amount - self.paid_amount

    @property
    def is_overdue(self):
        return self.due_date < timezone.localdate() and self.balance > 0


class LatePenaltyRule(TenantModel):
    class Kind(models.TextChoices):
        PERCENTAGE = "percentage", "Percentage"
        FIXED = "fixed", "Fixed amount"

    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=20, choices=Kind.choices,
                            default=Kind.PERCENTAGE)
    value = models.DecimalField(max_digits=10, decimal_places=2,
                                validators=[MinValueValidator(0)])
    grace_days = models.PositiveIntegerField(default=0)
    cap_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True,
                                     blank=True, validators=[MinValueValidator(0)])
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def penalty_for(self, base):
        amount = (base * self.value / Decimal("100")).quantize(Decimal("0.01")) \
            if self.kind == self.Kind.PERCENTAGE else self.value
        if self.cap_amount is not None:
            amount = min(amount, self.cap_amount)
        return amount


class LatePenalty(TenantModel):
    invoice = models.ForeignKey(Invoice, null=True, blank=True,
                                on_delete=models.CASCADE,
                                related_name="late_penalties")
    instalment = models.ForeignKey(Instalment, null=True, blank=True,
                                   on_delete=models.CASCADE,
                                   related_name="late_penalties")
    rule = models.ForeignKey(LatePenaltyRule, null=True, blank=True,
                             on_delete=models.SET_NULL,
                             related_name="penalties")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    applied_on = models.DateField(default=timezone.localdate)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-applied_on", "-pk"]

    def __str__(self):
        return f"Penalty {self.amount}"


class StudentCredit(TenantModel):
    """A credit balance, usually the unused part of an overpayment."""

    student = models.ForeignKey("academics.Student", on_delete=models.CASCADE,
                                related_name="credits")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment = models.ForeignKey(Payment, null=True, blank=True,
                                on_delete=models.SET_NULL,
                                related_name="credits")
    reason = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name="credits_created")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created", "-pk"]

    def __str__(self):
        return f"Credit {self.amount} for {self.student}"

    @property
    def applied(self):
        return self.applications.aggregate(t=Sum("amount"))["t"] or Decimal("0")

    @property
    def balance(self):
        return self.amount - self.applied


class CreditApplication(TenantModel):
    credit = models.ForeignKey(StudentCredit, on_delete=models.CASCADE,
                               related_name="applications")
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE,
                                related_name="credit_applications")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name="credit_applications_created")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created", "-pk"]

    def __str__(self):
        return f"{self.amount} to {self.invoice}"


# ---------- reversals and refunds ----------


class Refund(TenantModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Waiting for approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    payment = models.ForeignKey(Payment, on_delete=models.PROTECT,
                                related_name="refunds")
    amount = models.DecimalField(max_digits=12, decimal_places=2,
                                 validators=[MinValueValidator(0.01)])
    reason = models.CharField(max_length=200)
    status = models.CharField(max_length=20, choices=Status.choices,
                              default=Status.PENDING)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL,
                                     related_name="refunds_requested")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL,
                                    related_name="refunds_approved")
    created = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    journal_entry = models.ForeignKey(JournalEntry, null=True, blank=True,
                                      on_delete=models.SET_NULL,
                                      related_name="refunds")

    class Meta:
        ordering = ["-created", "-pk"]

    def __str__(self):
        return f"Refund {self.amount} on {self.payment.receipt_no}"

    def clean(self):
        super().clean()
        if self.payment_id and self.amount:
            available = self.payment.net_amount
            if self.pk:
                old = Refund.objects.filter(pk=self.pk).values_list(
                    "amount", flat=True).first() or Decimal("0")
                available += old
            if self.amount > available:
                raise ValidationError(
                    {"amount": f"Refund exceeds the payment balance of {available}."})

    def save(self, *a, **kw):
        if self._state.adding:
            self.full_clean()
        super().save(*a, **kw)


# ---------- suppliers, expenses and budgets ----------


class Supplier(TenantModel):
    name = models.CharField(max_length=160)
    code = models.CharField(max_length=30, blank=True)
    contact_name = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Expense(TenantModel):
    supplier = models.ForeignKey(Supplier, null=True, blank=True,
                                 on_delete=models.SET_NULL,
                                 related_name="expenses")
    department = models.ForeignKey("core.Department", null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name="expenses")
    financial_year = models.ForeignKey(FinancialYear, null=True, blank=True,
                                       on_delete=models.SET_NULL,
                                       related_name="expenses")
    description = models.CharField(max_length=200)
    category = models.CharField(max_length=80, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2,
                                 validators=[MinValueValidator(0.01)])
    tax_category = models.CharField(max_length=20, choices=TaxCategory.choices,
                                    default=TaxCategory.EXEMPT)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    currency = models.CharField(max_length=10, blank=True)
    exchange_rate = models.DecimalField(max_digits=14, decimal_places=6,
                                        default=Decimal("1"))
    base_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    date = models.DateField(default=timezone.localdate)
    reference = models.CharField(max_length=80, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name="expenses_created")
    created = models.DateTimeField(auto_now_add=True)
    journal_entry = models.ForeignKey(JournalEntry, null=True, blank=True,
                                      on_delete=models.SET_NULL,
                                      related_name="expenses")

    class Meta:
        ordering = ["-date", "-pk"]

    def __str__(self):
        return f"{self.description} {self.amount}"

    def save(self, *a, **kw):
        adding = self._state.adding
        from .services import tax_rate_in_force

        rate = tax_rate_in_force(self.institution, self.tax_category, self.date)
        self.tax_rate = rate
        gross = Decimal(self.amount or 0)
        if rate and gross:
            self.tax_amount = (gross - gross / (Decimal("1") + rate / Decimal("100"))
                               ).quantize(Decimal("0.01"))
        else:
            self.tax_amount = Decimal("0")
        if not self.currency:
            self.currency = (getattr(self.institution, "base_currency", "")
                             or getattr(self.institution, "currency", "") or "USD")
        if self.base_amount is None:
            self.base_amount = gross * (self.exchange_rate or Decimal("1"))
        super().save(*a, **kw)
        if adding:
            from .services import post_expense
            post_expense(self)


class Budget(TenantModel):
    name = models.CharField(max_length=120)
    department = models.ForeignKey("core.Department", null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name="budgets")
    financial_year = models.ForeignKey(FinancialYear, null=True, blank=True,
                                       on_delete=models.SET_NULL,
                                       related_name="budgets")
    amount = models.DecimalField(max_digits=12, decimal_places=2,
                                 validators=[MinValueValidator(0)])
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name="budgets_created")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} {self.amount}"


class CollectionTarget(TenantModel):
    """A collections goal for a period, used by the collection-vs-target report."""

    name = models.CharField(max_length=120)
    financial_year = models.ForeignKey(FinancialYear, null=True, blank=True,
                                       on_delete=models.SET_NULL,
                                       related_name="collection_targets")
    department = models.ForeignKey("core.Department", null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name="collection_targets")
    start_date = models.DateField(default=timezone.localdate)
    end_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=12, decimal_places=2,
                                 validators=[MinValueValidator(0)])
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name="collection_targets_created")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start_date", "name"]

    def __str__(self):
        return f"{self.name} ({self.amount})"

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError(
                {"end_date": "The target must end on or after it starts."})


# ---------- bank reconciliation ----------


class BankStatementLine(TenantModel):
    date = models.DateField()
    description = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reference = models.CharField(max_length=80, blank=True)
    matched_payment = models.ForeignKey(Payment, null=True, blank=True,
                                        on_delete=models.SET_NULL,
                                        related_name="bank_lines")
    imported_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name="bank_lines_imported")

    class Meta:
        ordering = ["date", "pk"]

    def __str__(self):
        return f"{self.date} {self.description} {self.amount}"

    @property
    def is_matched(self):
        return self.matched_payment_id is not None


# ---------- online payment gateway ----------


class GatewayTransaction(TenantModel):
    """One attempt to pay online, kept for polling and idempotency."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    provider = models.CharField(max_length=40, default="paynow")
    reference = models.CharField(max_length=80)
    idempotency_key = models.CharField(max_length=120, unique=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices,
                              default=Status.PENDING)
    payload = models.JSONField(default=dict, blank=True)
    invoice = models.ForeignKey(Invoice, null=True, blank=True,
                                on_delete=models.SET_NULL,
                                related_name="gateway_transactions")
    payment = models.ForeignKey(Payment, null=True, blank=True,
                                on_delete=models.SET_NULL,
                                related_name="gateway_transactions")
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created", "-pk"]

    def __str__(self):
        return f"{self.provider} {self.reference} {self.status}"
