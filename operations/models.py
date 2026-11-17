"""Administration modules: HR, payroll, procurement, estates, security,
compliance, marketing and governance.

Every row is a tenant row, so the usual institution filter and the module
feature flags apply without any extra plumbing.
"""

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F
from django.utils import timezone

from core.models import TenantModel
from core.uploads import document_upload_validator


def _zero():
    return Decimal("0")


def _percent(amount, rate):
    return (Decimal(amount or 0) * Decimal(rate or 0) / Decimal("100")).quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# Human resources
# ---------------------------------------------------------------------------


class StaffContract(TenantModel):
    class Kind(models.TextChoices):
        PERMANENT = "permanent", "Permanent"
        FIXED = "fixed", "Fixed term"
        PART_TIME = "part_time", "Part time"
        CONSULTANCY = "consultancy", "Consultancy"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ENDED = "ended", "Ended"

    staff = models.ForeignKey(
        "core.StaffProfile", on_delete=models.CASCADE, related_name="contracts"
    )
    reference = models.CharField(max_length=40, blank=True)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.PERMANENT)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    salary = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-start_date", "pk"]

    def clean(self):
        super().clean()
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "A contract cannot end before it starts."})

    def save(self, *a, **kw):
        super().save(*a, **kw)
        if not self.reference:
            self.reference = f"CON-{self.pk:05d}"
            super().save(update_fields=["reference"])

    def __str__(self):
        return self.reference or f"Contract for {self.staff}"


class StaffDocument(TenantModel):
    class Kind(models.TextChoices):
        CONTRACT = "contract", "Contract"
        CERTIFICATE = "certificate", "Certificate"
        IDENTITY = "identity", "Identity"
        APPRAISAL = "appraisal", "Appraisal"
        OTHER = "other", "Other"

    staff = models.ForeignKey(
        "core.StaffProfile", on_delete=models.CASCADE, related_name="documents"
    )
    title = models.CharField(max_length=150)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.OTHER)
    file = models.FileField(
        upload_to="operations/staff/", blank=True,
        validators=[document_upload_validator])
    confidential = models.BooleanField(default=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="staff_documents_uploaded",
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["staff__user__last_name", "title"]

    def __str__(self):
        return self.title


class LeaveRequest(TenantModel):
    class Kind(models.TextChoices):
        ANNUAL = "annual", "Annual leave"
        SICK = "sick", "Sick leave"
        MATERNITY = "maternity", "Maternity / paternity"
        COMPASSIONATE = "compassionate", "Compassionate"
        UNPAID = "unpaid", "Unpaid"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting for approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    staff = models.ForeignKey(
        "core.StaffProfile", on_delete=models.CASCADE, related_name="leave_requests"
    )
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.ANNUAL)
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="leave_decisions",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-start_date", "pk"]

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "Leave cannot end before it starts."})

    @property
    def days(self):
        if not self.start_date or not self.end_date:
            return 0
        return (self.end_date - self.start_date).days + 1

    def __str__(self):
        return f"{self.staff} {self.start_date} to {self.end_date}"


class Appraisal(TenantModel):
    staff = models.ForeignKey(
        "core.StaffProfile", on_delete=models.CASCADE, related_name="appraisals"
    )
    period = models.CharField(max_length=60)
    score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    strengths = models.TextField(blank=True)
    improvements = models.TextField(blank=True)
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="appraisals_reviewed",
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created", "pk"]

    def __str__(self):
        return f"{self.staff} - {self.period}"


class TrainingRecord(TenantModel):
    staff = models.ForeignKey(
        "core.StaffProfile", on_delete=models.CASCADE, related_name="training"
    )
    title = models.CharField(max_length=150)
    provider = models.CharField(max_length=150, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )
    completed = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-start_date", "pk"]

    def __str__(self):
        return self.title


class DisciplinaryCase(TenantModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        HEARING = "hearing", "Hearing"
        CLOSED = "closed", "Closed"
        APPEALED = "appealed", "Appealed"

    staff = models.ForeignKey(
        "core.StaffProfile", on_delete=models.CASCADE, related_name="disciplinary_cases"
    )
    date = models.DateField(default=timezone.localdate)
    category = models.CharField(max_length=80)
    summary = models.TextField()
    outcome = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    confidential = models.BooleanField(default=True)

    class Meta:
        ordering = ["-date", "pk"]
        verbose_name_plural = "disciplinary cases"

    def __str__(self):
        return f"{self.staff} - {self.category}"


class Vacancy(TenantModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"
        FILLED = "filled", "Filled"

    title = models.CharField(max_length=150)
    department = models.ForeignKey(
        "core.Department",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="vacancies",
    )
    description = models.TextField(blank=True)
    closes_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)

    class Meta:
        ordering = ["-closes_on", "pk"]
        verbose_name_plural = "vacancies"

    def __str__(self):
        return self.title


class JobApplication(TenantModel):
    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        SHORTLISTED = "shortlisted", "Shortlisted"
        INTERVIEWED = "interviewed", "Interviewed"
        OFFERED = "offered", "Offered"
        REJECTED = "rejected", "Rejected"

    vacancy = models.ForeignKey(Vacancy, on_delete=models.CASCADE, related_name="applications")
    name = models.CharField(max_length=150)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    cover_letter = models.TextField(blank=True)
    cv = models.FileField(
        upload_to="operations/cvs/", blank=True,
        validators=[document_upload_validator])
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RECEIVED)
    applied_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-applied_at", "pk"]

    def __str__(self):
        return f"{self.name} - {self.vacancy}"


# ---------------------------------------------------------------------------
# Payroll
# ---------------------------------------------------------------------------


class SalaryStructure(TenantModel):
    staff = models.ForeignKey(
        "core.StaffProfile", on_delete=models.CASCADE, related_name="salary_structures"
    )
    basic = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    housing_allowance = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )
    transport_allowance = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )
    other_allowance = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )
    other_deduction = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )
    bank_name = models.CharField(max_length=80, blank=True)
    bank_account = models.CharField(max_length=40, blank=True)
    effective_from = models.DateField(default=timezone.localdate)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["staff__user__last_name", "staff__user__first_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "staff"], name="uniq_salary_structure_staff"
            ),
        ]

    @property
    def allowances(self):
        return self.housing_allowance + self.transport_allowance + self.other_allowance

    @property
    def gross(self):
        return self.basic + self.allowances

    def __str__(self):
        return f"{self.staff} - {self.basic}"


class PayrollRule(TenantModel):
    class Kind(models.TextChoices):
        TAX = "tax", "Tax"
        PENSION = "pension", "Pension"
        OTHER = "other", "Other deduction"

    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.TAX)
    name = models.CharField(max_length=100)
    rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    applies_to_gross = models.BooleanField(
        default=True, help_text="Off means the rate applies to basic pay only."
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["kind", "name"]

    @classmethod
    def total_rate(cls, kind):
        return (
            cls.objects.filter(kind=kind, is_active=True).aggregate(total=models.Sum("rate"))[
                "total"
            ]
            or _zero()
        )

    def __str__(self):
        return f"{self.name} ({self.rate}%)"


class PayrollRun(TenantModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    name = models.CharField(max_length=120)
    start_date = models.DateField()
    end_date = models.DateField()
    pay_date = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="payroll_runs_created",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="payroll_runs_approved",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    posted_journal = models.ForeignKey(
        "finance.JournalEntry",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="payroll_runs",
    )

    class Meta:
        ordering = ["-pay_date", "-pk"]

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "The period cannot end before it starts."})

    @property
    def reference(self):
        return f"PAY-{self.pay_date:%Y%m}-{self.pk:04d}" if self.pk else "PAY-NEW"

    @property
    def total_gross(self):
        return self.payslips.aggregate(t=models.Sum(F("basic") + F("allowances")))["t"] or _zero()

    @property
    def total_net(self):
        return self.payslips.aggregate(t=models.Sum("net"))["t"] or _zero()

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"


class Payslip(TenantModel):
    run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name="payslips")
    staff = models.ForeignKey(
        "core.StaffProfile", on_delete=models.CASCADE, related_name="payslips"
    )
    basic = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    allowances = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    pension = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    other_deduction = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    net = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        ordering = ["staff__user__last_name", "staff__user__first_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "run", "staff"], name="uniq_payslip_staff_run"
            ),
        ]

    @property
    def gross(self):
        return self.basic + self.allowances

    @property
    def deductions(self):
        return self.tax + self.pension + self.other_deduction

    def recalculate(self, tax_rate, pension_rate):
        self.tax = _percent(self.gross, tax_rate)
        self.pension = _percent(self.basic, pension_rate)
        self.net = self.gross - self.deductions
        return self

    def __str__(self):
        return f"{self.staff} {self.run.name}"


# ---------------------------------------------------------------------------
# Procurement and stores
# ---------------------------------------------------------------------------


def _line_total(quantity, unit_price):
    return (Decimal(quantity or 0) * Decimal(unit_price or 0)).quantize(Decimal("0.01"))


class Requisition(TenantModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        ORDERED = "ordered", "Ordered"
        RECEIVED = "received", "Received"
        CANCELLED = "cancelled", "Cancelled"

    department = models.ForeignKey(
        "core.Department",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="requisitions",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="requisitions_requested",
    )
    title = models.CharField(max_length=150)
    justification = models.TextField(blank=True)
    needed_by = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created", "pk"]

    @property
    def estimated_total(self):
        return sum((line.total for line in self.lines.all()), _zero())

    def __str__(self):
        return self.title


class RequisitionLine(TenantModel):
    requisition = models.ForeignKey(Requisition, on_delete=models.CASCADE, related_name="lines")
    description = models.CharField(max_length=150)
    quantity = models.PositiveIntegerField(default=1)
    estimated_unit_cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )

    @property
    def total(self):
        return _line_total(self.quantity, self.estimated_unit_cost)

    def __str__(self):
        return self.description


class PurchaseOrder(TenantModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        SENT = "sent", "Sent to supplier"
        RECEIVED = "received", "Received"
        CANCELLED = "cancelled", "Cancelled"

    supplier = models.ForeignKey(
        "finance.Supplier", on_delete=models.PROTECT, related_name="purchase_orders"
    )
    requisition = models.ForeignKey(
        Requisition,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="purchase_orders",
    )
    number = models.CharField(max_length=40, blank=True)
    order_date = models.DateField(default=timezone.localdate)
    expected_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="purchase_orders_approved",
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-order_date", "-pk"]

    def save(self, *a, **kw):
        super().save(*a, **kw)
        if not self.number:
            self.number = f"PO-{self.pk:05d}"
            super().save(update_fields=["number"])

    @property
    def total(self):
        return sum((line.total for line in self.lines.all()), _zero())

    def __str__(self):
        return self.number or f"Order for {self.supplier}"


class PurchaseOrderLine(TenantModel):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    description = models.CharField(max_length=150)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )

    @property
    def total(self):
        return _line_total(self.quantity, self.unit_price)

    def __str__(self):
        return self.description


class GoodsReceivedNote(TenantModel):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="receipts")
    received_on = models.DateField(default=timezone.localdate)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="goods_received",
    )
    reference = models.CharField(max_length=40, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-received_on", "-pk"]
        verbose_name = "goods received note"
        verbose_name_plural = "goods received notes"

    def __str__(self):
        return f"GRN for {self.order}"


class Quotation(TenantModel):
    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        AWARDED = "awarded", "Awarded"
        REJECTED = "rejected", "Rejected"

    requisition = models.ForeignKey(
        Requisition, on_delete=models.CASCADE, related_name="quotations"
    )
    supplier = models.ForeignKey(
        "finance.Supplier", on_delete=models.PROTECT, related_name="quotations"
    )
    number = models.CharField(max_length=40, blank=True)
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )
    valid_until = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RECEIVED)
    awarded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="quotations_awarded",
    )
    awarded_at = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["amount", "pk"]

    def save(self, *a, **kw):
        super().save(*a, **kw)
        if not self.number:
            self.number = f"QUO-{self.pk:05d}"
            super().save(update_fields=["number"])

    @property
    def line_total(self):
        return sum((line.total for line in self.lines.all()), _zero())

    def __str__(self):
        return f"{self.supplier} quote for {self.requisition}"


class QuotationLine(TenantModel):
    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE, related_name="lines")
    description = models.CharField(max_length=150)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )

    @property
    def total(self):
        return _line_total(self.quantity, self.unit_price)

    def __str__(self):
        return self.description


class SupplierInvoice(TenantModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        MATCHED = "matched", "Matched"
        DISPUTED = "disputed", "Disputed"
        CANCELLED = "cancelled", "Cancelled"

    supplier = models.ForeignKey(
        "finance.Supplier", on_delete=models.PROTECT, related_name="supplier_invoices"
    )
    order = models.ForeignKey(
        PurchaseOrder,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="supplier_invoices",
    )
    receipt = models.ForeignKey(
        GoodsReceivedNote,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="supplier_invoices",
    )
    number = models.CharField(max_length=60)
    invoice_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField(null=True, blank=True)
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    description = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    variance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    match_note = models.TextField(blank=True)
    expense = models.OneToOneField(
        "finance.Expense",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="supplier_invoice",
    )
    matched_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="supplier_invoices_matched",
    )
    matched_at = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-invoice_date", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "supplier", "number"],
                name="uniq_supplier_invoice_number",
            ),
        ]

    def clean(self):
        super().clean()
        if self.due_date and self.invoice_date and self.due_date < self.invoice_date:
            raise ValidationError({"due_date": "The due date cannot be before the invoice date."})
        if self.order_id and self.receipt_id and self.receipt.order_id != self.order_id:
            raise ValidationError(
                {"receipt": "The receipt does not belong to the selected purchase order."}
            )

    def __str__(self):
        return f"{self.number} from {self.supplier}"


class StockItem(TenantModel):
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=150)
    category = models.CharField(max_length=80, blank=True)
    unit = models.CharField(max_length=20, default="unit")
    quantity = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )
    reorder_level = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )
    unit_cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["institution", "code"], name="uniq_stock_item_code"),
        ]

    @property
    def is_low(self):
        return self.quantity <= self.reorder_level

    @property
    def value(self):
        return (self.quantity * self.unit_cost).quantize(Decimal("0.01"))

    def __str__(self):
        return f"{self.code} - {self.name}"


class StockMovement(TenantModel):
    class Kind(models.TextChoices):
        IN = "in", "Received"
        OUT = "out", "Issued"
        ADJUST = "adjust", "Stock count adjustment"

    item = models.ForeignKey(StockItem, on_delete=models.CASCADE, related_name="movements")
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.IN)
    quantity = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="stock_movements",
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created", "-pk"]

    def clean(self):
        super().clean()
        if (
            self.kind == self.Kind.OUT
            and self.pk is None
            and self.item_id
            and self.quantity > self.item.quantity
        ):
            raise ValidationError({"quantity": "There is not enough stock to issue."})

    def save(self, *a, **kw):
        adding = self._state.adding
        super().save(*a, **kw)
        if not adding:
            return
        if self.kind == self.Kind.IN:
            StockItem.all_objects.filter(pk=self.item_id).update(
                quantity=F("quantity") + self.quantity
            )
        elif self.kind == self.Kind.OUT:
            StockItem.all_objects.filter(pk=self.item_id).update(
                quantity=F("quantity") - self.quantity
            )
        else:
            StockItem.all_objects.filter(pk=self.item_id).update(quantity=self.quantity)

    def __str__(self):
        return f"{self.get_kind_display()} {self.quantity} {self.item}"


class Asset(TenantModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "In use"
        REPAIR = "repair", "Under repair"
        RETIRED = "retired", "Retired"
        DISPOSED = "disposed", "Disposed"

    name = models.CharField(max_length=150)
    tag = models.CharField(max_length=40, blank=True)
    category = models.CharField(max_length=80, blank=True)
    department = models.ForeignKey(
        "core.Department", null=True, blank=True, on_delete=models.SET_NULL, related_name="assets"
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assets_assigned",
    )
    purchased_on = models.DateField(null=True, blank=True)
    cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "tag"], condition=~models.Q(tag=""), name="uniq_asset_tag"
            ),
        ]

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Estates
# ---------------------------------------------------------------------------


class WorkOrder(TenantModel):
    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In progress"
        ON_HOLD = "on_hold", "On hold"
        DONE = "done", "Done"
        CANCELLED = "cancelled", "Cancelled"

    title = models.CharField(max_length=150)
    location = models.CharField(max_length=120)
    category = models.CharField(max_length=80, blank=True)
    priority = models.CharField(max_length=20, choices=Priority.choices, default=Priority.MEDIUM)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    description = models.TextField(blank=True)
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="work_orders_reported",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="work_orders_assigned",
    )
    opened_on = models.DateField(default=timezone.localdate)
    due_on = models.DateField(null=True, blank=True)
    completed_on = models.DateField(null=True, blank=True)
    cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )

    class Meta:
        ordering = ["-opened_on", "-pk"]

    def __str__(self):
        return self.title


class CleaningSchedule(TenantModel):
    class Frequency(models.TextChoices):
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"
        MONTHLY = "monthly", "Monthly"
        TERMLY = "termly", "Termly"

    area = models.CharField(max_length=120)
    frequency = models.CharField(max_length=20, choices=Frequency.choices, default=Frequency.DAILY)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="cleaning_areas",
    )
    last_done = models.DateField(null=True, blank=True)
    next_due = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["next_due", "area"]

    def __str__(self):
        return self.area


class UtilityReading(TenantModel):
    class Kind(models.TextChoices):
        ELECTRICITY = "electricity", "Electricity"
        WATER = "water", "Water"
        GAS = "gas", "Gas"
        OTHER = "other", "Other"

    meter_name = models.CharField(max_length=100)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.ELECTRICITY)
    reading_date = models.DateField(default=timezone.localdate)
    reading = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )
    cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-reading_date", "meter_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "meter_name", "reading_date"], name="uniq_utility_reading"
            ),
        ]

    def __str__(self):
        return f"{self.meter_name} {self.reading_date}"


class MaintenancePlan(TenantModel):
    title = models.CharField(max_length=150)
    asset = models.ForeignKey(
        Asset, null=True, blank=True, on_delete=models.SET_NULL, related_name="maintenance_plans"
    )
    frequency_days = models.PositiveIntegerField(default=30)
    last_done = models.DateField(null=True, blank=True)
    next_due = models.DateField(null=True, blank=True)
    instructions = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["next_due", "title"]

    def save(self, *a, **kw):
        if self.last_done and not self.next_due:
            self.next_due = self.last_done + timedelta(days=self.frequency_days)
        super().save(*a, **kw)

    def __str__(self):
        return self.title


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


class Visitor(TenantModel):
    full_name = models.CharField(max_length=150)
    id_number = models.CharField(max_length=60, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    host = models.CharField(max_length=150, blank=True)
    purpose = models.CharField(max_length=200, blank=True)
    badge_no = models.CharField(max_length=30, blank=True)
    time_in = models.DateTimeField(default=timezone.now)
    time_out = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-time_in", "-pk"]

    @property
    def is_on_site(self):
        return self.time_out is None

    def __str__(self):
        return self.full_name


class GatePass(TenantModel):
    class Status(models.TextChoices):
        OUT = "out", "Out"
        RETURNED = "returned", "Returned"
        LOST = "lost", "Lost"

    person_name = models.CharField(max_length=150)
    item = models.CharField(max_length=200)
    purpose = models.CharField(max_length=200, blank=True)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="gate_passes_issued",
    )
    issued_at = models.DateTimeField(default=timezone.now)
    due_back = models.DateTimeField(null=True, blank=True)
    returned_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OUT)

    class Meta:
        ordering = ["-issued_at", "-pk"]

    def __str__(self):
        return f"{self.person_name}: {self.item}"


class StudentSignOut(TenantModel):
    student = models.ForeignKey(
        "academics.Student", on_delete=models.CASCADE, related_name="sign_outs"
    )
    reason = models.CharField(max_length=200)
    guardian_contact = models.CharField(max_length=120, blank=True)
    out_at = models.DateTimeField(default=timezone.now)
    expected_back = models.DateTimeField(null=True, blank=True)
    returned_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sign_outs_approved",
    )

    class Meta:
        ordering = ["-out_at", "-pk"]

    @property
    def is_out(self):
        return self.returned_at is None

    def __str__(self):
        return f"{self.student} - {self.reason}"


class Incident(TenantModel):
    class Severity(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        CRITICAL = "critical", "Critical"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        INVESTIGATING = "investigating", "Investigating"
        CLOSED = "closed", "Closed"

    date = models.DateField(default=timezone.localdate)
    location = models.CharField(max_length=120)
    category = models.CharField(max_length=80)
    severity = models.CharField(max_length=20, choices=Severity.choices, default=Severity.MEDIUM)
    description = models.TextField()
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="incidents_reported",
    )
    action_taken = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)

    class Meta:
        ordering = ["-date", "-pk"]

    def __str__(self):
        return f"{self.date} {self.category} at {self.location}"


# ---------------------------------------------------------------------------
# Compliance
# ---------------------------------------------------------------------------


class Policy(TenantModel):
    title = models.CharField(max_length=150)
    code = models.CharField(max_length=30)
    category = models.CharField(max_length=80, blank=True)
    version = models.CharField(max_length=20, default="1.0")
    body = models.TextField(blank=True)
    file = models.FileField(
        upload_to="operations/policies/", blank=True,
        validators=[document_upload_validator])
    effective_date = models.DateField(default=timezone.localdate)
    review_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["title"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "code", "version"], name="uniq_policy_version"
            ),
        ]
        verbose_name_plural = "policies"

    def __str__(self):
        return f"{self.code} {self.title} v{self.version}"


class PolicyAcknowledgement(TenantModel):
    policy = models.ForeignKey(Policy, on_delete=models.CASCADE, related_name="acknowledgements")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="policy_acknowledgements"
    )
    acknowledged_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-acknowledged_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "policy", "user"], name="uniq_policy_acknowledgement"
            ),
        ]

    def __str__(self):
        return f"{self.user} acknowledged {self.policy}"


class ComplianceAudit(TenantModel):
    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"

    title = models.CharField(max_length=150)
    area = models.CharField(max_length=100)
    audit_date = models.DateField(default=timezone.localdate)
    auditor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="compliance_audits",
    )
    findings = models.TextField(blank=True)
    score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED)
    due_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-audit_date", "-pk"]

    def __str__(self):
        return self.title


class Risk(TenantModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        MONITORING = "monitoring", "Monitoring"
        CLOSED = "closed", "Closed"

    title = models.CharField(max_length=150)
    category = models.CharField(max_length=80, blank=True)
    likelihood = models.PositiveSmallIntegerField(
        default=3, validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    impact = models.PositiveSmallIntegerField(
        default=3, validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="risks_owned",
    )
    mitigation = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    review_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-impact", "-likelihood", "title"]

    @property
    def score(self):
        return self.likelihood * self.impact

    @property
    def rating(self):
        if self.score >= 15:
            return "High"
        if self.score >= 8:
            return "Medium"
        return "Low"

    def __str__(self):
        return self.title


class DataRequest(TenantModel):
    class Kind(models.TextChoices):
        ACCESS = "access", "Access"
        CORRECTION = "correction", "Correction"
        DELETION = "deletion", "Deletion"
        OBJECTION = "objection", "Objection"

    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        REFUSED = "refused", "Refused"

    requester_name = models.CharField(max_length=150)
    email = models.EmailField(blank=True)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.ACCESS)
    details = models.TextField(blank=True)
    received_on = models.DateField(default=timezone.localdate)
    due_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RECEIVED)
    completed_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["due_on", "-received_on"]

    def __str__(self):
        return f"{self.requester_name} - {self.get_kind_display()}"


# ---------------------------------------------------------------------------
# Marketing
# ---------------------------------------------------------------------------


class Enquiry(TenantModel):
    class Status(models.TextChoices):
        NEW = "new", "New"
        CONTACTED = "contacted", "Contacted"
        CONVERTED = "converted", "Converted"
        CLOSED = "closed", "Closed"

    name = models.CharField(max_length=150)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    source = models.CharField(max_length=80, blank=True)
    interest = models.CharField(max_length=150, blank=True)
    message = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="enquiries_assigned",
    )
    follow_up_on = models.DateField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created", "-pk"]
        verbose_name_plural = "enquiries"

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"


class Campaign(TenantModel):
    class Kind(models.TextChoices):
        EMAIL = "email", "Email"
        SMS = "sms", "SMS"
        SOCIAL = "social", "Social media"
        EVENT = "event", "Event"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    name = models.CharField(max_length=150)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.EMAIL)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    budget = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["-start_date", "name"]

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "A campaign cannot end before it starts."})

    def __str__(self):
        return self.name


class OpenDay(TenantModel):
    title = models.CharField(max_length=150)
    date = models.DateField()
    venue = models.CharField(max_length=120, blank=True)
    description = models.TextField(blank=True)
    capacity = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["date", "title"]
        verbose_name = "open day"
        verbose_name_plural = "open days"

    def __str__(self):
        return f"{self.title} ({self.date})"


class Newsletter(TenantModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SCHEDULED = "scheduled", "Scheduled"
        SENT = "sent", "Sent"

    title = models.CharField(max_length=150)
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    scheduled_for = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="newsletters_created",
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created", "-pk"]

    def __str__(self):
        return self.title


# ---------------------------------------------------------------------------
# Governance
# ---------------------------------------------------------------------------


class Meeting(TenantModel):
    class Kind(models.TextChoices):
        BOARD = "board", "Board"
        COMMITTEE = "committee", "Committee"
        MANAGEMENT = "management", "Management"
        GENERAL = "general", "General meeting"

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        HELD = "held", "Held"
        CANCELLED = "cancelled", "Cancelled"

    title = models.CharField(max_length=150)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.COMMITTEE)
    date = models.DateField()
    time = models.TimeField(null=True, blank=True)
    location = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SCHEDULED)
    agenda = models.TextField(blank=True)
    minutes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="meetings_created",
    )

    class Meta:
        ordering = ["-date", "-pk"]

    def __str__(self):
        return f"{self.title} ({self.date})"


class Resolution(TenantModel):
    class Status(models.TextChoices):
        PROPOSED = "proposed", "Proposed"
        PASSED = "passed", "Passed"
        REJECTED = "rejected", "Rejected"
        DEFERRED = "deferred", "Deferred"

    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="resolutions")
    title = models.CharField(max_length=150)
    body = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROPOSED)
    decided_on = models.DateField(null=True, blank=True)
    votes_for = models.PositiveIntegerField(default=0)
    votes_against = models.PositiveIntegerField(default=0)
    votes_abstain = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-meeting__date", "title"]

    def __str__(self):
        return self.title


class ActionItem(TenantModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In progress"
        DONE = "done", "Done"
        CANCELLED = "cancelled", "Cancelled"

    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="action_items")
    resolution = models.ForeignKey(
        Resolution, null=True, blank=True, on_delete=models.SET_NULL, related_name="action_items"
    )
    title = models.CharField(max_length=150)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="action_items_owned",
    )
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    completed_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["due_date", "title"]

    def __str__(self):
        return self.title


class GovernanceLevy(TenantModel):
    name = models.CharField(max_length=150)
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    due_date = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-due_date", "name"]
        verbose_name = "governance levy"
        verbose_name_plural = "governance levies"

    def __str__(self):
        return self.name
