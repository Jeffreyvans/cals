"""Services for the administration modules.

The screens call these functions so payroll, leave decisions and newsletters
behave the same from a form, a command or a test.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.email_types import ANNOUNCEMENT
from core.notifications import notify, queue_message
from finance.models import Expense
from finance.services import post_payroll

from .models import (
    LeaveRequest,
    Newsletter,
    PayrollRule,
    Payslip,
    PurchaseOrder,
    PurchaseOrderLine,
    Quotation,
    Requisition,
    SalaryStructure,
    SupplierInvoice,
)


@transaction.atomic
def generate_payslips(run):
    """Create one payslip per active salary structure, skipping existing rows."""
    tax_rate = PayrollRule.total_rate(PayrollRule.Kind.TAX)
    pension_rate = PayrollRule.total_rate(PayrollRule.Kind.PENSION)
    existing = set(run.payslips.values_list("staff_id", flat=True))
    made = 0
    for structure in SalaryStructure.objects.filter(is_active=True).select_related(
        "staff", "staff__user"
    ):
        if structure.staff_id in existing:
            continue
        slip = Payslip(
            institution=run.institution,
            run=run,
            staff=structure.staff,
            basic=structure.basic,
            allowances=structure.allowances,
            other_deduction=structure.other_deduction,
        )
        slip.recalculate(tax_rate, pension_rate)
        slip.save()
        made += 1
    return made


def approve_payroll_run(run, user):
    if run.status != run.Status.DRAFT:
        raise ValidationError("Only a draft payroll run can be approved.")
    if not run.payslips.exists():
        raise ValidationError("Add the payslips before approving the run.")
    run.status = run.Status.APPROVED
    run.approved_by = user
    run.approved_at = timezone.now()
    run.save(update_fields=["status", "approved_by", "approved_at"])
    return run


@transaction.atomic
def post_payroll_run(run, user):
    """Post the net pay to the ledger and mark the run paid."""
    if run.status != run.Status.APPROVED:
        raise ValidationError("Approve the payroll run before posting it.")
    total = run.payslips.aggregate(t=Sum("net"))["t"] or Decimal("0")
    if total <= 0:
        raise ValidationError("There is nothing to post for this payroll run.")
    entry = post_payroll(
        run.institution,
        total,
        description=f"Payroll: {run.name}",
        date=run.pay_date,
        reference=run.reference,
        created_by=user,
    )
    run.status = run.Status.PAID
    run.posted_journal = entry
    run.save(update_fields=["status", "posted_journal"])
    return entry


@transaction.atomic
def decide_leave(leave, user, approve, note=""):
    if leave.status != LeaveRequest.Status.PENDING:
        raise ValidationError("This leave request has already been decided.")
    leave.status = LeaveRequest.Status.APPROVED if approve else LeaveRequest.Status.REJECTED
    leave.decided_by = user
    leave.decided_at = timezone.now()
    leave.decision_note = note
    leave.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
    staff_user = getattr(leave.staff, "user", None)
    if staff_user is not None:
        notify(
            staff_user,
            f"Leave request {leave.get_status_display().lower()}",
            (
                f"Your {leave.get_kind_display().lower()} from {leave.start_date} "
                f"to {leave.end_date} was {leave.get_status_display().lower()}."
            ),
            link="",
            category="hr",
            institution=leave.institution,
        )
    return leave


@transaction.atomic
def send_newsletter(newsletter, actor=None, on_progress=None):
    """Queue the newsletter to every active student and staff email address."""
    from django.conf import settings

    from academics.models import Student
    from core.models import StaffProfile
    from core.notifications import bulk_email_limit_reached, record_bulk_email

    if newsletter.status == Newsletter.Status.SENT:
        raise ValidationError("This newsletter has already been sent.")
    recipients = []
    for user in Student.objects.filter(
        status__in=[Student.Status.ACTIVE, Student.Status.ENROLLED]
    ).values_list("user__email", flat=True):
        if user:
            recipients.append(user)
    for email in StaffProfile.objects.values_list("user__email", flat=True):
        if email:
            recipients.append(email)
    recipients = sorted(set(recipients))
    if len(recipients) > getattr(settings, "EMAIL_BULK_LIMIT", 500) or bulk_email_limit_reached(newsletter.institution):
        raise ValidationError("Too many recipients. Try again later or send a smaller newsletter.")
    record_bulk_email(newsletter.institution, len(recipients))
    total = len(recipients)
    for index, email in enumerate(recipients, start=1):
        queue_message(
            "email",
            email,
            newsletter.body,
            subject=newsletter.subject or newsletter.title,
            institution=newsletter.institution,
            related_object=newsletter,
            email_type=ANNOUNCEMENT,
        )
        if on_progress is not None:
            on_progress(index, total)
    newsletter.status = Newsletter.Status.SENT
    newsletter.sent_at = timezone.now()
    newsletter.save(update_fields=["status", "sent_at"])
    return len(recipients)


@transaction.atomic
def award_quotation(quotation, user):
    """Award a received quote, reject the others and raise a purchase order."""
    if quotation.status != Quotation.Status.RECEIVED:
        raise ValidationError("Only a received quotation can be awarded.")
    requisition = quotation.requisition
    if requisition.status != Requisition.Status.APPROVED:
        raise ValidationError("Approve the requisition before awarding a quotation.")
    quotation.requisition.quotations.filter(status=Quotation.Status.RECEIVED).exclude(
        pk=quotation.pk
    ).update(status=Quotation.Status.REJECTED)
    quotation.status = Quotation.Status.AWARDED
    quotation.awarded_by = user
    quotation.awarded_at = timezone.now()
    quotation.save(update_fields=["status", "awarded_by", "awarded_at"])

    order = PurchaseOrder.objects.create(
        institution=quotation.institution,
        supplier=quotation.supplier,
        requisition=requisition,
        order_date=timezone.localdate(),
        status=PurchaseOrder.Status.APPROVED,
        notes=f"Awarded from quotation {quotation.number}.",
        approved_by=user,
    )
    lines = list(quotation.lines.all())
    if lines:
        for line in lines:
            PurchaseOrderLine.objects.create(
                institution=quotation.institution,
                order=order,
                description=line.description,
                quantity=line.quantity,
                unit_price=line.unit_price,
            )
    else:
        PurchaseOrderLine.objects.create(
            institution=quotation.institution,
            order=order,
            description=requisition.title,
            quantity=1,
            unit_price=quotation.amount,
        )
    requisition.status = Requisition.Status.ORDERED
    requisition.save(update_fields=["status"])
    return order


def supplier_invoice_expected_total(invoice):
    """The order or receipt total an invoice is matched against."""
    if invoice.order_id:
        return invoice.order.total
    if invoice.receipt_id:
        return invoice.receipt.order.total
    return invoice.amount


@transaction.atomic
def match_supplier_invoice(invoice, actor, create_expense=True):
    """Compare an invoice to its order and post the expense when it agrees.

    A difference leaves the invoice disputed and records the variance so
    someone can chase the supplier before anything reaches the ledger.
    """
    if invoice.status not in (SupplierInvoice.Status.PENDING, SupplierInvoice.Status.DISPUTED):
        raise ValidationError("Only a pending or disputed invoice can be matched.")
    expected = supplier_invoice_expected_total(invoice)
    invoice.variance = (invoice.amount - expected).quantize(Decimal("0.01"))
    invoice.matched_by = actor
    invoice.matched_at = timezone.now()
    if invoice.variance != 0:
        invoice.status = SupplierInvoice.Status.DISPUTED
        invoice.match_note = (
            f"Invoice {invoice.amount} differs from the matched total {expected} "
            f"by {invoice.variance}."
        )
        invoice.save(
            update_fields=["variance", "matched_by", "matched_at", "status", "match_note"]
        )
        return invoice
    if create_expense and not invoice.expense_id:
        expense = Expense(
            institution=invoice.institution,
            supplier=invoice.supplier,
            description=invoice.description or f"Supplier invoice {invoice.number}",
            amount=invoice.amount,
            date=invoice.invoice_date,
            reference=invoice.number,
            created_by=actor,
        )
        expense.save()
        invoice.expense = expense
    invoice.status = SupplierInvoice.Status.MATCHED
    invoice.match_note = "Matched to the order total."
    invoice.save(
        update_fields=[
            "variance",
            "matched_by",
            "matched_at",
            "status",
            "match_note",
            "expense",
        ]
    )
    return invoice
