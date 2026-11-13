"""Library loans: renewals, overdue notices, fines and QR lookups."""

import uuid
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from core.notifications import notify
from finance.models import Invoice

from .models import Book, Loan

MAX_RENEWALS = 2
RENEWAL_DAYS = 14


def find_book(code):
    """Find a book by its QR token, ISBN or shelf label.

    A scanned QR code carries the book's token, but staff may also type the
    ISBN or shelf. An invalid UUID is simply treated as no match.
    """
    code = (code or "").strip()
    if not code:
        return None
    condition = Q(isbn__iexact=code) | Q(shelf__iexact=code)
    try:
        condition |= Q(qr_token=uuid.UUID(code))
    except (ValueError, AttributeError):
        pass
    return Book.objects.filter(condition).first()


def qr_payload(book, request=None):
    """The text stored inside a book's QR code."""
    url = reverse("library:qr_lookup") + f"?code={book.qr_token}"
    if request is not None:
        return request.build_absolute_uri(url)
    return url


def renew_loan(loan, days=RENEWAL_DAYS, max_renewals=MAX_RENEWALS):
    if loan.status != Loan.Status.BORROWED:
        raise ValidationError("Only a book still on loan can be renewed.")
    if loan.is_overdue:
        raise ValidationError("Overdue loans cannot be renewed; return the book first.")
    if loan.renewals >= max_renewals:
        raise ValidationError(
            f"This loan has already been renewed {max_renewals} times."
        )
    loan.due_on = max(loan.due_on, timezone.localdate()) + timedelta(days=days)
    loan.renewals += 1
    loan.save(update_fields=["due_on", "renewals"])
    return loan


def mark_overdue_loans(today=None):
    today = today or timezone.localdate()
    return Loan.objects.filter(status=Loan.Status.BORROWED, due_on__lt=today).update(
        status=Loan.Status.OVERDUE
    )


def send_overdue_notices(today=None):
    """Notify each borrower at most once a day and flag the loan overdue."""
    today = today or timezone.localdate()
    loans = (
        Loan.objects.filter(
            status__in=[Loan.Status.BORROWED, Loan.Status.OVERDUE], due_on__lt=today
        )
        .exclude(overdue_notified_on=today)
        .select_related("book", "student__user")
    )
    sent = 0
    for loan in loans:
        notify(
            loan.student.user,
            "Library book overdue",
            f"'{loan.book}' was due on {loan.due_on}. Please return it.",
            link=reverse("library:list", args=["loans"]),
            category="library",
            institution=loan.institution,
        )
        loan.status = Loan.Status.OVERDUE
        loan.overdue_notified_on = today
        loan.save(update_fields=["status", "overdue_notified_on"])
        sent += 1
    return sent


def assess_loan_fine(loan, amount=None, actor=None):
    """Create one invoice for a loan fine and link it to the loan."""
    if loan.fine_invoice_id:
        raise ValidationError("This loan has already been charged.")
    amount = Decimal(amount if amount is not None else loan.fine)
    if amount <= 0:
        raise ValidationError("Set a fine amount before charging it.")
    invoice = Invoice.objects.create(
        institution=loan.institution,
        student=loan.student,
        description=f"Library fine: {loan.book}",
        amount=amount,
        due_date=timezone.localdate(),
        created_by=actor,
    )
    loan.fine = amount
    loan.fine_invoice = invoice
    loan.save(update_fields=["fine", "fine_invoice"])
    return invoice
