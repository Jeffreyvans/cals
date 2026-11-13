"""Finance services: one place for the ledger, balances, fees and refunds.

Every money event posts a balanced journal entry here. Views and models call
these functions instead of writing journal lines themselves.
"""

import csv
import time
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO

from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

from .models import (
    DEFAULT_VAT_RATE,
    DOCUMENT_PREFIXES,
    Account,
    BankStatementLine,
    CollectionTarget,
    CreditApplication,
    DebitNote,
    Discount,
    DocumentSequence,
    Expense,
    FeeItem,
    FinancialYear,
    Invoice,
    JournalEntry,
    JournalLine,
    LatePenalty,
    Payment,
    Refund,
    StudentCredit,
    TaxCategory,
    TaxRate,
    Waiver,
)

ZERO = Decimal("0")

DEFAULT_ACCOUNTS = [
    ("1000", "Cash", Account.Type.ASSET),
    ("1010", "Bank", Account.Type.ASSET),
    ("1100", "Accounts receivable", Account.Type.ASSET),
    ("1200", "Inventory", Account.Type.ASSET),
    ("1500", "Fixed assets", Account.Type.ASSET),
    ("2000", "Accounts payable", Account.Type.LIABILITY),
    ("2100", "Student credits", Account.Type.LIABILITY),
    ("2200", "Tax payable", Account.Type.LIABILITY),
    ("3000", "Capital", Account.Type.EQUITY),
    ("3100", "Retained earnings", Account.Type.EQUITY),
    ("4000", "Tuition income", Account.Type.INCOME),
    ("4010", "Boarding income", Account.Type.INCOME),
    ("4020", "Transport income", Account.Type.INCOME),
    ("4030", "Examination income", Account.Type.INCOME),
    ("4040", "Levy income", Account.Type.INCOME),
    ("4090", "Other income", Account.Type.INCOME),
    ("5000", "Salaries", Account.Type.EXPENSE),
    ("5010", "Utilities", Account.Type.EXPENSE),
    ("5020", "Supplies", Account.Type.EXPENSE),
    ("5100", "Discounts and bursaries", Account.Type.EXPENSE),
    ("5090", "Other expenses", Account.Type.EXPENSE),
]

FEE_INCOME_ACCOUNTS = {
    FeeItem.Kind.TUITION: "4000",
    FeeItem.Kind.BOARDING: "4010",
    FeeItem.Kind.TRANSPORT: "4020",
    FeeItem.Kind.EXAM: "4030",
    FeeItem.Kind.LEVY: "4040",
    FeeItem.Kind.OTHER: "4090",
}

CASH_ACCOUNTS = {
    Payment.Method.CASH: "1000",
    Payment.Method.BANK: "1010",
    Payment.Method.CARD: "1010",
    Payment.Method.MOBILE: "1010",
}


def _institution_for(instance):
    return instance.institution


def tax_rate_in_force(institution, category, day=None):
    """The VAT rate to use for a category on a given day.

    An institution that is not VAT-registered never charges VAT. A registered
    institution charges the dated rate in force, falling back to the standard
    rate when no row has been entered yet.
    """
    day = day or timezone.localdate()
    if institution is None:
        return ZERO
    if getattr(institution, "vat_status", "") != "registered":
        return ZERO
    if category != TaxCategory.STANDARD:
        return ZERO
    stored = TaxRate.rate_on(institution, category, day)
    return stored if stored is not None else DEFAULT_VAT_RATE


def next_document_number(institution, document_type, day=None):
    """Allocate the next gap-free number for a document type.

    The counter is advanced with an atomic ``F()`` update inside a row lock, so
    simultaneous requests each get a distinct number. On SQLite (where
    ``select_for_update`` is a no-op) the unique constraint on the sequence row
    catches the rare double insert and the call retries. SQLite can also refuse
    a write with a "database is locked" error while another request holds the
    table; that is a transient contention error, so it is retried too.
    """
    day = day or timezone.localdate()
    label = str(day.year)
    prefix = DOCUMENT_PREFIXES.get(document_type, str(document_type)[:3].upper())
    for _attempt in range(8):
        try:
            with transaction.atomic():
                sequence, _created = DocumentSequence.all_objects.select_for_update(
                ).get_or_create(
                    institution=institution, document_type=document_type,
                    financial_year=label, defaults={"last_number": 0})
                DocumentSequence.all_objects.filter(pk=sequence.pk).update(
                    last_number=F("last_number") + 1)
                sequence.refresh_from_db()
                return f"{prefix}-{label}-{sequence.last_number:06d}"
        except IntegrityError:
            continue
        except OperationalError as exc:
            message = str(exc).lower()
            if "locked" not in message and "busy" not in message:
                raise
            time.sleep(0.02 * (_attempt + 1))
            continue
    raise ValidationError(
        "Could not allocate a document number right now. Please try again.")


def ensure_chart_of_accounts(institution):
    """Create the default chart of accounts for an institution."""
    created = 0
    for code, name, kind in DEFAULT_ACCOUNTS:
        _account, was_created = Account.all_objects.get_or_create(
            institution=institution, code=code,
            defaults={"name": name, "type": kind})
        created += int(was_created)
    return created


def account(institution, code):
    ensure_chart_of_accounts(institution)
    return Account.all_objects.get(institution=institution, code=code)


def _financial_year_for(institution, day):
    return FinancialYear.all_objects.filter(
        institution=institution, start_date__lte=day, end_date__gte=day).first()


def _check_period_open(institution, day):
    year = _financial_year_for(institution, day)
    if year is not None and year.is_closed:
        raise ValidationError(
            f"The financial year {year.name} is closed; no entry can be posted to {day}.")
    return year


@transaction.atomic
def post_journal(institution, description, lines, date=None, reference="",
                 source_type="", source_id="", created_by=None):
    """Post one balanced entry. `lines` are (account_code, debit, credit, note)."""
    day = date or timezone.localdate()
    if source_type and source_id:
        existing = JournalEntry.all_objects.filter(
            institution=institution, source_type=source_type,
            source_id=str(source_id)).first()
        if existing is not None:
            return existing
    year = _check_period_open(institution, day)
    clean_lines = []
    for code, debit, credit, note in lines:
        debit = Decimal(debit or 0)
        credit = Decimal(credit or 0)
        if debit or credit:
            clean_lines.append((code, debit, credit, note))
    if not clean_lines:
        return None
    total_debit = sum((line[1] for line in clean_lines), ZERO)
    total_credit = sum((line[2] for line in clean_lines), ZERO)
    if total_debit != total_credit:
        raise ValidationError(
            f"Journal does not balance: debits {total_debit}, credits {total_credit}.")
    entry = JournalEntry.all_objects.create(
        institution=institution, date=day, description=description[:200],
        reference=reference[:80], source_type=source_type,
        source_id=str(source_id) if source_id else "", financial_year=year,
        created_by=created_by if getattr(created_by, "is_authenticated", False) else None)
    for code, debit, credit, note in clean_lines:
        JournalLine.all_objects.create(
            institution=institution, entry=entry, account=account(institution, code),
            debit=debit, credit=credit, description=note[:200])
    return entry


def post_invoice(invoice):
    kind = invoice.fee_item.kind if invoice.fee_item_id else FeeItem.Kind.TUITION
    income_code = FEE_INCOME_ACCOUNTS.get(kind, "4000")
    return post_journal(
        _institution_for(invoice), f"Invoice: {invoice.description}",
        [("1100", invoice.amount, 0, invoice.description),
         (income_code, 0, invoice.amount, invoice.description)],
        date=invoice.created.date() if invoice.created else None,
        reference=f"INV{invoice.pk}", source_type="Invoice",
        source_id=invoice.pk, created_by=invoice.created_by)


def post_payment(payment):
    amount = payment.base_amount if payment.base_amount is not None else payment.amount
    cash_code = CASH_ACCOUNTS.get(payment.method, "1000")
    return post_journal(
        _institution_for(payment), f"Payment {payment.receipt_no}",
        [(cash_code, amount, 0, payment.invoice.description),
         ("1100", 0, amount, payment.invoice.description)],
        date=payment.paid_on.date() if payment.paid_on else None,
        reference=payment.receipt_no, source_type="Payment",
        source_id=payment.pk, created_by=payment.received_by)


def post_waiver(waiver):
    return post_journal(
        _institution_for(waiver), f"Credit note {waiver.note_no}",
        [("5100", waiver.amount, 0, waiver.reason),
         ("1100", 0, waiver.amount, waiver.reason)],
        date=waiver.created.date() if waiver.created else None,
        reference=waiver.note_no, source_type="Waiver",
        source_id=waiver.pk, created_by=waiver.recorded_by)


def post_debit_note(note):
    """A debit note raises an extra charge: the mirror of a credit note."""
    kind = note.invoice.fee_item.kind if note.invoice.fee_item_id else FeeItem.Kind.TUITION
    income_code = FEE_INCOME_ACCOUNTS.get(kind, "4000")
    return post_journal(
        _institution_for(note), f"Debit note {note.note_no}",
        [("1100", note.amount, 0, note.reason),
         (income_code, 0, note.amount, note.reason)],
        date=note.created.date() if note.created else None,
        reference=note.note_no, source_type="DebitNote",
        source_id=note.pk, created_by=note.recorded_by)


def post_discount(discount):
    return post_journal(
        _institution_for(discount), f"Discount: {discount.name}",
        [("5100", discount.amount, 0, discount.name),
         ("1100", 0, discount.amount, discount.name)],
        reference=f"DISC{discount.pk}", source_type="Discount",
        source_id=discount.pk, created_by=discount.created_by)


def post_credit(credit):
    """A credit balance is a liability until it is used."""
    return post_journal(
        _institution_for(credit), f"Credit balance for {credit.student}",
        [("1000", credit.amount, 0, credit.reason),
         ("2100", 0, credit.amount, credit.reason)],
        reference=f"CRD{credit.pk}", source_type="StudentCredit",
        source_id=credit.pk, created_by=credit.created_by)


def post_credit_application(application):
    return post_journal(
        _institution_for(application), f"Credit applied to invoice {application.invoice_id}",
        [("2100", application.amount, 0, application.invoice.description),
         ("1100", 0, application.amount, application.invoice.description)],
        reference=f"CAP{application.pk}", source_type="CreditApplication",
        source_id=application.pk, created_by=application.created_by)


def post_refund(refund):
    cash_code = CASH_ACCOUNTS.get(refund.payment.method, "1000")
    entry = post_journal(
        _institution_for(refund), f"Refund {refund.payment.receipt_no}",
        [("1100", refund.amount, 0, refund.reason),
         (cash_code, 0, refund.amount, refund.reason)],
        reference=f"REF{refund.pk}", source_type="Refund",
        source_id=refund.pk, created_by=refund.approved_by)
    refund.journal_entry = entry
    refund.save(update_fields=["journal_entry"])
    return entry


def post_expense(expense):
    credit_code = "2000" if expense.supplier_id else "1000"
    entry = post_journal(
        _institution_for(expense), f"Expense: {expense.description}",
        [("5090", expense.amount, 0, expense.description),
         (credit_code, 0, expense.amount, expense.description)],
        date=expense.date, reference=expense.reference or f"EXP{expense.pk}",
        source_type="Expense", source_id=expense.pk, created_by=expense.created_by)
    if entry is not None:
        Expense.all_objects.filter(pk=expense.pk).update(journal_entry=entry)
    return entry


def post_payroll(institution, amount, description="Payroll run", date=None,
                 reference="", created_by=None):
    return post_journal(
        institution, description,
        [("5000", amount, 0, description), ("1000", 0, amount, description)],
        date=date, reference=reference, source_type="Payroll",
        source_id=reference, created_by=created_by)


# ---------- student balances ----------


def student_balance(student):
    """The one balance used by the student page, portals and reports."""
    billed = paid = waived = discounted = credited = refunded = ZERO
    for invoice in Invoice.objects.filter(student=student):
        billed += invoice.amount + invoice.debited
        paid += invoice.paid
        refunded += invoice.refunded
        waived += invoice.waived
        discounted += invoice.discounted
        credited += invoice.credit_applied
    return billed - (paid - refunded) - waived - discounted - credited


def balances_by_student():
    """Bulk version of :func:`student_balance` for list screens."""
    billed = dict(Invoice.objects.values_list("student_id").annotate(t=Sum("amount")))
    for student_id, extra in DebitNote.objects.values_list(
            "invoice__student_id").annotate(t=Sum("amount")):
        billed[student_id] = billed.get(student_id, ZERO) + extra
    paid = dict(Payment.objects.values_list("invoice__student_id").annotate(t=Sum("amount")))
    refunded = dict(Refund.objects.filter(status=Refund.Status.APPROVED)
                    .values_list("payment__invoice__student_id").annotate(t=Sum("amount")))
    waived = dict(Waiver.objects.values_list("invoice__student_id").annotate(t=Sum("amount")))
    discounted = dict(Discount.objects.values_list("student_id").annotate(t=Sum("amount")))
    credited = dict(CreditApplication.objects.values_list("invoice__student_id")
                    .annotate(t=Sum("amount")))
    return billed, paid, refunded, waived, discounted, credited


def credit_balance(student):
    total = StudentCredit.objects.filter(student=student).aggregate(
        t=Sum("amount"))["t"] or ZERO
    used = CreditApplication.objects.filter(credit__student=student).aggregate(
        t=Sum("amount"))["t"] or ZERO
    return total - used


# ---------- fees and invoices ----------


def eligible_students(structure):
    from academics.models import Student

    students = Student.objects.filter(status=Student.Status.ACTIVE)
    if structure.programme_id:
        students = students.filter(programme_id=structure.programme_id)
    if structure.school_class_id:
        students = students.filter(school_class_id=structure.school_class_id)
    return students.select_related("user", "programme", "school_class")


def generate_invoices(structure, term, preview=True, created_by=None):
    """Create one invoice per fee item for every eligible student.

    With ``preview=True`` nothing is written; the same rows are returned so the
    screen can show exactly what will happen.
    """
    items = list(structure.items.all())
    due = term.end_date if term else timezone.localdate()
    rows = []
    existing = set(
        Invoice.objects.filter(structure=structure, term=term)
        .values_list("student_id", "fee_item_id"))
    for student in eligible_students(structure):
        for item in items:
            already = (student.pk, item.pk) in existing
            rows.append({
                "student": student,
                "item": item,
                "amount": item.amount,
                "already": already,
                "create": not already,
            })
    if not preview:
        created = 0
        for row in rows:
            if not row["create"]:
                continue
            Invoice.objects.create(
                student=row["student"], description=row["item"].name,
                amount=row["amount"], due_date=due, term=term,
                structure=structure, fee_item=row["item"],
                created_by=created_by)
            created += 1
        return {"rows": rows, "created": created,
                "total": sum((r["amount"] for r in rows if r["create"]), ZERO)}
    return {"rows": rows, "created": 0,
            "total": sum((r["amount"] for r in rows if r["create"]), ZERO)}


# ---------- payments, credits and refunds ----------


@transaction.atomic
def receive_payment(student, invoice, amount, method=Payment.Method.CASH,
                    reference="", payer_name="", received_by=None,
                    currency="", exchange_rate=Decimal("1")):
    """Take a payment, applying the invoice part and keeping any excess as credit."""
    amount = Decimal(amount)
    if amount <= 0:
        raise ValidationError("Amount must be above zero.")
    balance = max(invoice.balance, ZERO)
    applied = min(amount, balance)
    payment = None
    if applied > 0:
        payment = Payment(
            invoice=invoice, amount=applied, method=method, reference=reference,
            payer_name=payer_name, currency=currency,
            exchange_rate=exchange_rate or Decimal("1"),
            received_by=received_by)
        payment.save()
    excess = amount - applied
    credit = None
    if excess > 0:
        credit = StudentCredit.objects.create(
            student=student, amount=excess, payment=payment,
            reason=f"Overpayment on {invoice.description}"[:200],
            created_by=received_by)
        post_credit(credit)
    return payment, credit


@transaction.atomic
def apply_credit(credit, invoice, amount=None, created_by=None):
    amount = min(Decimal(amount or credit.balance), credit.balance,
                 max(invoice.balance, ZERO))
    if amount <= 0:
        raise ValidationError("There is no credit or invoice balance to apply.")
    application = CreditApplication.objects.create(
        credit=credit, invoice=invoice, amount=amount, created_by=created_by)
    post_credit_application(application)
    return application


@transaction.atomic
def request_refund(payment, amount, reason, requested_by=None):
    refund = Refund(payment=payment, amount=amount, reason=reason,
                    requested_by=requested_by)
    refund.save()
    return refund


@transaction.atomic
def approve_refund(refund, approved_by=None):
    if refund.status != Refund.Status.PENDING:
        raise ValidationError("This refund has already been decided.")
    refund.status = Refund.Status.APPROVED
    refund.approved_by = approved_by
    refund.approved_at = timezone.now()
    refund.save(update_fields=["status", "approved_by", "approved_at"])
    post_refund(refund)
    return refund


@transaction.atomic
def reject_refund(refund, approved_by=None):
    if refund.status != Refund.Status.PENDING:
        raise ValidationError("This refund has already been decided.")
    refund.status = Refund.Status.REJECTED
    refund.approved_by = approved_by
    refund.approved_at = timezone.now()
    refund.save(update_fields=["status", "approved_by", "approved_at"])
    return refund


# ---------- discounts, sponsors and instalments ----------


def apply_discount(discount, invoice=None):
    """Apply a discount rule to an invoice and post the ledger entry."""
    target = invoice or discount.invoice
    if target is None:
        raise ValidationError("A discount needs an invoice to apply to.")
    discount.invoice = target
    discount.amount = min(discount.compute_amount(target), max(target.balance, ZERO))
    discount.save()
    post_discount(discount)
    return discount


def sponsor_statement(sponsor, start=None, end=None):
    discounts = Discount.objects.filter(sponsor=sponsor).select_related(
        "student__user", "invoice")
    if start:
        discounts = discounts.filter(created__date__gte=start)
    if end:
        discounts = discounts.filter(created__date__lte=end)
    rows = list(discounts.order_by("created"))
    return {"rows": rows, "total": sum((d.amount for d in rows), ZERO)}


def overdue_instalments(as_of=None):
    from .models import Instalment

    as_of = as_of or timezone.localdate()
    return Instalment.objects.filter(due_date__lt=as_of, amount__gt=0).exclude(
        paid_amount__gte=F("amount"))


@transaction.atomic
def apply_late_penalties(rule, as_of=None, created_by=None):
    """Apply a rule to every overdue instalment once. Returns the penalties made."""
    as_of = as_of or timezone.localdate()
    from .models import Instalment

    made = []
    for instalment in Instalment.objects.filter(
            due_date__lt=as_of - timedelta(days=rule.grace_days)):
        if instalment.balance <= 0:
            continue
        if LatePenalty.objects.filter(instalment=instalment, rule=rule).exists():
            continue
        amount = rule.penalty_for(instalment.balance)
        if amount <= 0:
            continue
        penalty = LatePenalty.objects.create(
            instalment=instalment, rule=rule, amount=amount,
            note=f"Late payment on {instalment.plan.name}"[:200])
        Invoice.objects.create(
            student=instalment.plan.student, description=f"Late penalty: {rule.name}",
            amount=amount, due_date=as_of, term=instalment.plan.term,
            created_by=created_by)
        made.append(penalty)
    return made


@transaction.atomic
def reverse_journal_entry(entry, reason, actor=None, date=None):
    """Post the mirror entry that cancels `entry` without deleting history."""
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("A reason is required to reverse an entry.")
    existing = JournalEntry.all_objects.filter(
        institution=entry.institution, source_type="Reversal",
        source_id=str(entry.pk)).first()
    if existing is not None:
        raise ValidationError("This entry has already been reversed.")
    lines = [
        (line.account.code, line.credit, line.debit,
         f"Reversal: {line.description}") for line in entry.lines.all()
    ]
    reversal = post_journal(
        entry.institution, f"Reversal of {entry.number}: {reason}",
        lines, date=date or timezone.localdate(), reference=entry.number,
        source_type="Reversal", source_id=entry.pk, created_by=actor)
    from core.audit import log_action

    log_action(actor, "finance.journal.reversed", instance=entry,
               after={"reversal": reversal.number, "reason": reason},
               institution=entry.institution)
    return reversal


def send_overdue_reminders(as_of=None, send=True):
    """Notify students (and guardians) about overdue instalments.

    One in-app notification and at most one email/SMS per student per day, so
    running the job twice on the same day does not spam anybody.
    """
    from core.models import Notification, OutboundMessage

    as_of = as_of or timezone.localdate()
    grouped = {}
    for instalment in overdue_instalments(as_of).select_related(
            "plan__student__user", "plan__student__institution"):
        student = instalment.plan.student
        row = grouped.setdefault(student.pk, {"student": student,
                                              "balance": ZERO,
                                              "plans": []})
        row["balance"] += instalment.balance
        if instalment.plan.name not in row["plans"]:
            row["plans"].append(instalment.plan.name)

    summary = {"students": 0, "notifications": 0, "messages": 0}
    for row in grouped.values():
        student = row["student"]
        user = student.user
        title = "Overdue fees"
        body = (f"{row['balance']:.2f} is overdue on "
                f"{', '.join(row['plans'])}. Please pay to avoid penalties.")
        already_notified = Notification.objects.filter(
            user=user, category="finance", title=title,
            created__date=as_of).exists()
        if not already_notified:
            from core.notifications import notify, queue_message

            notify(user, title, body, "/me/", "finance")
            summary["notifications"] += 1
        contacts = []
        if user.email:
            contacts.append((OutboundMessage.Channel.EMAIL, user.email))
        if student.guardian_phone:
            contacts.append((OutboundMessage.Channel.SMS,
                             student.guardian_phone))
        for channel, recipient in contacts:
            sent_already = OutboundMessage.objects.filter(
                channel=channel, recipient=recipient, subject=title,
                related_object_type="Student",
                related_object_id=str(student.pk),
                created__date=as_of).exists()
            if sent_already:
                continue
            from core.notifications import queue_message

            queue_message(channel, recipient, body, subject=title,
                          related_object=student)
            summary["messages"] += 1
        summary["students"] += 1

    if send:
        from core.notifications import send_pending

        send_pending()
    return summary


# ---------- reports ----------


def trial_balance(institution=None, as_of=None):
    lines = JournalLine.objects.select_related("account")
    if as_of:
        lines = lines.filter(entry__date__lte=as_of)
    totals = {}
    for line in lines:
        row = totals.setdefault(line.account_id, {
            "account": line.account, "debit": ZERO, "credit": ZERO})
        row["debit"] += line.debit
        row["credit"] += line.credit
    rows = []
    for row in totals.values():
        account_obj = row["account"]
        balance = row["debit"] - row["credit"]
        if account_obj.type in (Account.Type.LIABILITY, Account.Type.EQUITY,
                                Account.Type.INCOME):
            balance = -balance
        row["balance"] = balance
        rows.append(row)
    rows.sort(key=lambda r: r["account"].code)
    return {
        "rows": rows,
        "total_debit": sum((r["debit"] for r in rows), ZERO),
        "total_credit": sum((r["credit"] for r in rows), ZERO),
    }


def _period_filter(queryset, start, end, field="entry__date"):
    if start:
        queryset = queryset.filter(**{f"{field}__gte": start})
    if end:
        queryset = queryset.filter(**{f"{field}__lte": end})
    return queryset


def income_statement(start=None, end=None):
    lines = _period_filter(JournalLine.objects.select_related("account"), start, end)
    income = {}
    expenses = {}
    for line in lines:
        if line.account.type == Account.Type.INCOME:
            income[line.account] = income.get(line.account, ZERO) + line.credit - line.debit
        elif line.account.type == Account.Type.EXPENSE:
            expenses[line.account] = expenses.get(line.account, ZERO) + line.debit - line.credit
    total_income = sum(income.values(), ZERO)
    total_expenses = sum(expenses.values(), ZERO)
    return {
        "income": sorted(income.items(), key=lambda pair: pair[0].code),
        "expenses": sorted(expenses.items(), key=lambda pair: pair[0].code),
        "total_income": total_income,
        "total_expenses": total_expenses,
        "net": total_income - total_expenses,
    }


def balance_sheet(as_of=None):
    lines = JournalLine.objects.select_related("account")
    if as_of:
        lines = lines.filter(entry__date__lte=as_of)
    buckets = {kind: {} for kind in Account.Type.values}
    for line in lines:
        account_obj = line.account
        amount = line.debit - line.credit
        if account_obj.type in (Account.Type.LIABILITY, Account.Type.EQUITY,
                                Account.Type.INCOME):
            amount = -amount
        buckets[account_obj.type][account_obj] = \
            buckets[account_obj.type].get(account_obj, ZERO) + amount
    assets = sum(buckets[Account.Type.ASSET].values(), ZERO)
    liabilities = sum(buckets[Account.Type.LIABILITY].values(), ZERO)
    equity = sum(buckets[Account.Type.EQUITY].values(), ZERO)
    income = sum(buckets[Account.Type.INCOME].values(), ZERO)
    expenses = sum(buckets[Account.Type.EXPENSE].values(), ZERO)
    profit = income - expenses
    return {
        "assets": sorted(buckets[Account.Type.ASSET].items(), key=lambda p: p[0].code),
        "liabilities": sorted(buckets[Account.Type.LIABILITY].items(), key=lambda p: p[0].code),
        "equity": sorted(buckets[Account.Type.EQUITY].items(), key=lambda p: p[0].code),
        "total_assets": assets,
        "total_liabilities": liabilities,
        "total_equity": equity + profit,
        "profit": profit,
    }


def debtors_ageing(as_of=None):
    as_of = as_of or timezone.localdate()
    buckets = {"current": ZERO, "30": ZERO, "60": ZERO, "90": ZERO}
    rows = []
    for invoice in Invoice.objects.select_related("student__user"):
        balance = invoice.balance
        if balance <= 0:
            continue
        days = (as_of - invoice.due_date).days
        key = "current" if days <= 0 else "30" if days <= 30 else "60" if days <= 60 else "90"
        buckets[key] += balance
        rows.append({"invoice": invoice, "days": days, "bucket": key,
                     "balance": balance})
    rows.sort(key=lambda row: (-row["days"], row["invoice"].pk))
    return {"rows": rows, "buckets": buckets, "total": sum(buckets.values(), ZERO)}


def vat_summary(start=None, end=None):
    """Output VAT charged on invoices and input VAT carried by expenses."""
    invoices = Invoice.objects.select_related("student")
    expenses = Expense.objects.select_related("supplier")
    if start:
        invoices = invoices.filter(created__date__gte=start)
        expenses = expenses.filter(date__gte=start)
    if end:
        invoices = invoices.filter(created__date__lte=end)
        expenses = expenses.filter(date__lte=end)
    output_vat = ZERO
    output_rows = []
    for invoice in invoices:
        if not invoice.tax_amount:
            continue
        output_vat += invoice.tax_amount
        output_rows.append({
            "document": invoice.number or f"INV{invoice.pk}",
            "date": invoice.created.date() if invoice.created else None,
            "party": invoice.student.name,
            "net": invoice.net_amount,
            "rate": invoice.tax_rate,
            "vat": invoice.tax_amount,
        })
    input_vat = ZERO
    input_rows = []
    for expense in expenses:
        if not expense.tax_amount:
            continue
        input_vat += expense.tax_amount
        input_rows.append({
            "document": expense.reference or f"EXP{expense.pk}",
            "date": expense.date,
            "party": expense.supplier.name if expense.supplier_id else "",
            "net": expense.amount - expense.tax_amount,
            "rate": expense.tax_rate,
            "vat": expense.tax_amount,
        })
    return {
        "output_rows": output_rows, "output_vat": output_vat,
        "input_rows": input_rows, "input_vat": input_vat,
        "net_vat": output_vat - input_vat,
    }


def income_by_fee_item(start=None, end=None):
    invoices = Invoice.objects.select_related("fee_item", "student")
    if start:
        invoices = invoices.filter(created__date__gte=start)
    if end:
        invoices = invoices.filter(created__date__lte=end)
    totals = {}
    for invoice in invoices:
        label = invoice.fee_item.name if invoice.fee_item_id else invoice.description
        totals[label] = totals.get(label, ZERO) + invoice.amount
    return sorted(totals.items(), key=lambda pair: pair[0])


def collection_target_rows(as_of=None):
    """Each target with what was collected in its period and the variance."""
    rows = []
    for target in CollectionTarget.objects.select_related(
            "financial_year", "department"):
        payments = Payment.objects.filter(
            paid_on__date__gte=target.start_date,
            paid_on__date__lte=target.end_date)
        if as_of:
            payments = payments.filter(paid_on__date__lte=as_of)
        collected = payments.aggregate(total=Sum("amount"))["total"] or ZERO
        amount = target.amount or ZERO
        percent = (collected / amount * 100) if amount else ZERO
        rows.append({
            "target": target,
            "collected": collected,
            "variance": collected - amount,
            "percent": percent.quantize(Decimal("0.1")),
        })
    return rows


def budget_variance(budget):
    expenses = Expense.objects.filter(department=budget.department) \
        if budget.department_id else Expense.objects.all()
    if budget.financial_year_id:
        expenses = expenses.filter(financial_year=budget.financial_year)
    spent = expenses.aggregate(t=Sum("amount"))["t"] or ZERO
    return {"budget": budget, "spent": spent, "variance": budget.amount - spent}


# ---------- bank reconciliation ----------


@transaction.atomic
def import_bank_statement(text, created_by=None, institution=None):
    """Read a CSV with date, description, amount and reference columns."""
    reader = csv.DictReader(StringIO(text))
    made = []
    for raw in reader:
        day = _parse_day(raw.get("date") or raw.get("Date"))
        if day is None:
            continue
        amount = _parse_amount(raw.get("amount") or raw.get("Amount"))
        if amount is None:
            continue
        line = BankStatementLine.objects.create(
            date=day, description=(raw.get("description") or raw.get("Description") or "")[:200],
            amount=amount, reference=(raw.get("reference") or raw.get("Reference") or "")[:80],
            created_by=created_by)
        if institution is not None and line.institution_id != institution.pk:
            BankStatementLine.all_objects.filter(pk=line.pk).update(institution=institution)
        made.append(line)
    return made


def match_bank_lines(lines=None):
    """Match a statement line to a payment by reference or exact amount and date."""
    lines = lines or BankStatementLine.objects.filter(matched_payment__isnull=True)
    matched = 0
    for line in lines:
        payment = None
        if line.reference:
            payment = Payment.objects.filter(
                Q(receipt_no__iexact=line.reference)
                | Q(reference__iexact=line.reference)).first()
        if payment is None:
            payment = Payment.objects.filter(
                amount=line.amount, paid_on__date=line.date,
                bank_lines__isnull=True).first()
        if payment is not None:
            line.matched_payment = payment
            line.save(update_fields=["matched_payment"])
            matched += 1
    return matched


def _parse_day(raw):
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return date.fromisoformat(raw) if fmt == "%Y-%m-%d" else \
                timezone.datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _parse_amount(raw):
    if raw is None:
        return None
    try:
        return Decimal(str(raw).replace(",", "").strip())
    except (ValueError, ArithmeticError):
        return None


def close_financial_year(year, user=None):
    year.is_closed = True
    year.closed_at = timezone.now()
    year.closed_by = user if getattr(user, "is_authenticated", False) else None
    year.save(update_fields=["is_closed", "closed_at", "closed_by"])
    return year


def open_financial_year(year):
    year.is_closed = False
    year.closed_at = None
    year.closed_by = None
    year.save(update_fields=["is_closed", "closed_at", "closed_by"])
    return year
