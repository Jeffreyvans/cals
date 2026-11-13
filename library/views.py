from io import BytesIO

import qrcode
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from core.access import user_has_any_code
from core.audit import log_action, log_permission_denied
from core.modules import is_enabled

from .models import Book, Loan
from .services import assess_loan_fine, find_book, qr_payload, renew_loan


def _guard(request, permissions=("library.manage", "library.borrow")):
    if not is_enabled("library", request.institution):
        raise Http404("This module is not switched on for your institution.")
    codes = (permissions,) if isinstance(permissions, str) else tuple(permissions)
    if not user_has_any_code(request.user, codes):
        log_permission_denied(request, codes)
        raise PermissionDenied(
            "Your role does not allow this action. Ask an administrator for access."
        )


@login_required
def loan_manage(request, pk):
    _guard(request)
    loan = get_object_or_404(
        Loan.objects.select_related("book", "student__user", "fine_invoice"), pk=pk
    )
    return render(
        request,
        "library/loan_manage.html",
        {
            "loan": loan,
            "can_manage": user_has_any_code(request.user, ("library.manage",)),
            "list_url": reverse("library:list", args=["loans"]),
            "renew_url": reverse("library:loan_renew", args=[loan.pk]),
            "fine_url": reverse("library:loan_fine", args=[loan.pk]),
        },
    )


@login_required
def loan_renew(request, pk):
    _guard(request, ("library.borrow", "library.manage"))
    loan = get_object_or_404(Loan.objects.all(), pk=pk)
    if request.method == "POST":
        try:
            renew_loan(loan)
            log_action(
                request.user,
                "library.loan.renewed",
                instance=loan,
                after={"due_on": str(loan.due_on), "renewals": loan.renewals},
                request=request,
            )
            messages.success(request, f"Loan renewed until {loan.due_on}.")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
    return redirect("library:loan_manage", pk=loan.pk)


@login_required
def loan_fine(request, pk):
    _guard(request, "library.manage")
    loan = get_object_or_404(Loan.objects.all(), pk=pk)
    if request.method == "POST":
        try:
            invoice = assess_loan_fine(loan, actor=request.user)
            log_action(
                request.user,
                "library.loan.fined",
                instance=loan,
                after={"invoice": invoice.pk, "amount": str(invoice.amount)},
                request=request,
            )
            messages.success(request, f"Fine charged to the student account ({invoice.amount}).")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
    return redirect("library:loan_manage", pk=loan.pk)


@login_required
def lookup(request):
    _guard(request)
    query = (request.GET.get("q") or "").strip()
    books = Book.objects.all()
    if query:
        books = books.filter(
            Q(title__icontains=query)
            | Q(author__icontains=query)
            | Q(isbn__icontains=query)
            | Q(shelf__icontains=query)
        )
    books = books.order_by("title")[:50]
    return render(
        request,
        "library/lookup.html",
        {"query": query, "books": books, "list_url": reverse("library:hub")},
    )


@login_required
def qr_lookup(request):
    """Resolve a scanned QR code (or typed token/ISBN) to one catalogue title."""
    _guard(request)
    code = (request.GET.get("code") or "").strip()
    book = find_book(code)
    loans = []
    if book is not None:
        loans = book.loans.select_related("student__user").exclude(
            status=Loan.Status.RETURNED
        )
    return render(
        request,
        "library/qr_lookup.html",
        {
            "code": code,
            "book": book,
            "loans": loans,
            "list_url": reverse("library:hub"),
            "lookup_url": reverse("library:lookup"),
            "qr_image_url": reverse("library:book_qr_png", args=[book.pk])
            if book is not None
            else "",
        },
    )


@login_required
def book_qr_png(request, pk):
    """Render the QR code for one book as a PNG."""
    _guard(request)
    book = get_object_or_404(Book.objects.all(), pk=pk)
    code = qrcode.QRCode(box_size=6, border=2)
    code.add_data(qr_payload(book, request))
    code.make(fit=True)
    buffer = BytesIO()
    code.make_image(fill_color="black", back_color="white").save(buffer, format="PNG")
    return HttpResponse(buffer.getvalue(), content_type="image/png")
