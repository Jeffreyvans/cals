import csv
from datetime import date as date_cls, datetime
from decimal import Decimal

from django.contrib import messages
from django.db.models import (DecimalField, ExpressionWrapper, F, Sum, Value)
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from accounts.decorators import role_required
from academics.models import Student, balances_by_student
from .forms import InvoiceForm, PaymentForm, WaiverForm
from .models import Invoice, MoneyLog, Payment, Waiver

AGING_LABELS = ("Not yet due", "1–30 days overdue", "31–60 days overdue",
                "61+ days overdue")
ZERO = Decimal("0")
BAL_FIELD = DecimalField(max_digits=12, decimal_places=2)


def _parse_date(raw):
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


@role_required("bursar")
def record_payment(request, pk):
    s = get_object_or_404(Student, pk=pk)
    form = PaymentForm(s, request.POST or None)
    if form.is_valid():
        p = form.save(commit=False); p.received_by = request.user; p.save()
        messages.success(request, f"Payment received. Receipt {p.receipt_no}.")
        return redirect("receipt", pk=p.pk)
    return render(request, "finance/payment_form.html", {"s": s, "form": form})

@role_required("bursar")
def create_invoice(request, pk):
    s = get_object_or_404(Student, pk=pk)
    form = InvoiceForm(request.POST or None)
    if form.is_valid():
        i = form.save(commit=False); i.student = s
        i.created_by = request.user; i.save()
        messages.success(request, "Invoice added to the student's account.")
        return redirect("student_detail", pk=pk)
    return render(request, "finance/invoice_form.html", {"s": s, "form": form})

@role_required("bursar")
def waive(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related("student__user"), pk=pk)
    form = WaiverForm(invoice, request.POST or None)
    if form.is_valid():
        w = form.save(commit=False)
        w.invoice = invoice
        w.recorded_by = request.user
        w.save()
        messages.success(request, f"Credit note {w.note_no} issued.")
        return redirect("credit_note", pk=w.pk)
    return render(request, "finance/waive_form.html", {"invoice": invoice, "form": form})

@role_required("bursar", "registrar")
def credit_note(request, pk):
    w = get_object_or_404(Waiver.objects.select_related("invoice__student__user",
                                                        "recorded_by"), pk=pk)
    return render(request, "finance/credit_note.html",
                  {"w": w, "s": w.invoice.student})

def _audit_entries(request):
    """Audit rows filtered by ?action= and the from/to dates."""
    action = request.GET.get("action", "")
    if action not in ("", *MoneyLog.Action.values):
        action = ""
    date_from, date_to, raw_from, raw_to = _date_filters(request)
    entries = MoneyLog.objects.select_related("actor", "student", "invoice")
    if action:
        entries = entries.filter(action=action)
    if date_from:
        entries = entries.filter(created__date__gte=date_from)
    if date_to:
        entries = entries.filter(created__date__lte=date_to)
    return entries, action, raw_from, raw_to


@role_required("bursar", "admin")
def audit_log(request):
    """Every money event, newest first."""
    entries, action, raw_from, raw_to = _audit_entries(request)
    return render(request, "finance/audit.html", {
        "entries": entries[:200], "action": action,
        "actions": MoneyLog.Action.choices,
        "raw_from": raw_from, "raw_to": raw_to})

@role_required("bursar", "registrar", "student")
def receipt(request, pk):
    p = get_object_or_404(Payment.objects.select_related("invoice__student__user", "received_by"), pk=pk)
    if request.user.role == "student" and p.invoice.student.user != request.user:
        return render(request, "403.html", status=403)
    return render(request, "finance/receipt.html", {"p": p, "s": p.invoice.student})


# ---------- reports ----------
def _debtors_data(show):
    """Rows (students annotated with amounts) and totals for the given filter."""
    billed, paid, waived = balances_by_student()
    rows = []
    for s in Student.objects.select_related("user", "programme", "school_class") \
            .filter(pk__in=billed.keys()) \
            .order_by("user__last_name", "user__first_name"):
        s.billed_amt = billed.get(s.pk, ZERO)
        s.paid_amt = paid.get(s.pk, ZERO)
        s.waived_amt = waived.get(s.pk, ZERO)
        s.owed = s.billed_amt - s.paid_amt - s.waived_amt
        if show == "owing" and s.owed <= 0:
            continue
        if show == "paid" and s.owed > 0:
            continue
        rows.append(s)
    totals = {"billed": sum((r.billed_amt for r in rows), ZERO),
              "paid": sum((r.paid_amt for r in rows), ZERO),
              "waived": sum((r.waived_amt for r in rows), ZERO),
              "owed": sum((r.owed for r in rows), ZERO)}
    return rows, totals


def _aging_data():
    """Unpaid invoice balances bucketed by how overdue they are."""
    today = date_cls.today()
    unpaid = (Invoice.objects
              .annotate(paid_amt=Sum("payments__amount"),
                        waived_amt=Sum("waivers__amount"))
              .annotate(bal=ExpressionWrapper(
                  F("amount") - Coalesce(F("paid_amt"), Value(ZERO))
                  - Coalesce(F("waived_amt"), Value(ZERO)),
                  output_field=BAL_FIELD))
              .filter(bal__gt=0)
              .values_list("due_date", "bal"))
    aging = {label: ZERO for label in AGING_LABELS}
    for due, bal in unpaid:
        days = (today - due).days
        key = (AGING_LABELS[0] if days <= 0 else AGING_LABELS[1] if days <= 30
               else AGING_LABELS[2] if days <= 60 else AGING_LABELS[3])
        aging[key] += bal
    return [(label, aging[label]) for label in AGING_LABELS]


def _clean_show(raw):
    return raw if raw in ("all", "owing", "paid") else "owing"


@role_required("bursar", "registrar")
def report_debtors(request):
    """Who owes what, how overdue it is, printable for the office wall."""
    show = _clean_show(request.GET.get("show", "owing"))
    rows, totals = _debtors_data(show)
    return render(request, "finance/debtors.html", {
        "rows": rows, "show": show, "totals": totals, "aging": _aging_data()})


@role_required("bursar", "registrar")
def export_debtors(request):
    show = _clean_show(request.GET.get("show", "owing"))
    rows, totals = _debtors_data(show)
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = (
        f'attachment; filename="debtors-{date_cls.today():%Y-%m-%d}.csv"')
    resp.write("\ufeff")  # UTF-8 BOM so Excel reads en-dashes correctly
    w = csv.writer(resp)
    w.writerow(["Student", "Student no", "Programme", "Class",
                "Billed", "Received", "Waived", "Owed"])
    for s in rows:
        w.writerow([s.name, s.student_no, s.programme.name,
                    s.class_label if s.school_class_id else "",
                    f"{s.billed_amt:.2f}", f"{s.paid_amt:.2f}",
                    f"{s.waived_amt:.2f}", f"{s.owed:.2f}"])
    w.writerow(["TOTAL", "", "", "",
                f"{totals['billed']:.2f}", f"{totals['paid']:.2f}",
                f"{totals['waived']:.2f}", f"{totals['owed']:.2f}"])
    return resp


def _date_filters(request):
    """Parse the from/to GET params; report errors and return usable dates."""
    raw_from = request.GET.get("from", "").strip()
    raw_to = request.GET.get("to", "").strip()
    for raw in (raw_from, raw_to):
        if raw and _parse_date(raw) is None:
            messages.error(request,
                f"'{raw}' is not a date. Use a format like 2026-03-31.")
    date_from = _parse_date(raw_from) if raw_from else None
    date_to = _parse_date(raw_to) if raw_to else None
    if date_from and date_to and date_from > date_to:
        messages.error(request, "The from date is after the to date.")
        date_from = date_to = None
    return date_from, date_to, raw_from, raw_to


def _collections_data(date_from, date_to):
    payments = Payment.objects.select_related("invoice__student__user",
                                              "received_by")
    if date_from:
        payments = payments.filter(paid_on__date__gte=date_from)
    if date_to:
        payments = payments.filter(paid_on__date__lte=date_to)
    payments = list(payments.order_by("-paid_on"))
    methods = {label: ZERO for _, label in Payment.Method.choices}
    students = set()
    for p in payments:
        methods[p.get_method_display()] += p.amount
        students.add(p.invoice.student_id)
    return payments, sum((p.amount for p in payments), ZERO), \
        len(students), list(methods.items())


@role_required("bursar", "registrar")
def report_collections(request):
    """Money received in a period, broken down by payment method."""
    date_from, date_to, raw_from, raw_to = _date_filters(request)
    payments, total, student_count, methods = _collections_data(date_from,
                                                                date_to)
    return render(request, "finance/collections.html", {
        "payments": payments, "total": total, "student_count": student_count,
        "methods": methods, "raw_from": raw_from, "raw_to": raw_to})


@role_required("bursar", "registrar")
def export_collections(request):
    date_from, date_to, _, _ = _date_filters(request)
    payments, total, _, methods = _collections_data(date_from, date_to)
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = (
        f'attachment; filename="collections-{date_cls.today():%Y-%m-%d}.csv"')
    resp.write("\ufeff")  # UTF-8 BOM so Excel reads en-dashes correctly
    w = csv.writer(resp)
    w.writerow(["Receipt", "Date", "Student", "Student no", "For",
                "Method", "Reference", "Amount", "Received by"])
    for p in payments:
        w.writerow([p.receipt_no, p.paid_on.strftime("%Y-%m-%d %H:%M"),
                    p.invoice.student.name, p.invoice.student.student_no,
                    p.invoice.description, p.get_method_display(),
                    p.reference or "", f"{p.amount:.2f}", p.received_by_name])
    w.writerow([])
    w.writerow(["By method"])
    for label, amount in methods:
        w.writerow([label, "", "", "", "", "", "", f"{amount:.2f}"])
    w.writerow(["TOTAL", "", "", "", "", "", "", f"{total:.2f}"])
    return resp


@role_required("bursar", "admin")
def export_audit(request):
    entries, _, _, _ = _audit_entries(request)
    entries = list(entries)
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = (
        f'attachment; filename="audit-log-{date_cls.today():%Y-%m-%d}.csv"')
    resp.write("\ufeff")  # UTF-8 BOM so Excel reads en-dashes correctly
    w = csv.writer(resp)
    w.writerow(["When", "Who", "Action", "Details", "Student",
                "Student no", "Reference", "Amount"])
    for e in entries:
        w.writerow([e.created.strftime("%Y-%m-%d %H:%M"), e.actor_name,
                    e.get_action_display(), e.description,
                    e.student.name if e.student else "",
                    e.student.student_no if e.student else "",
                    e.reference, f"{e.amount:.2f}"])
    w.writerow(["TOTAL", "", "", "", "", "", "",
                f"{sum((e.amount for e in entries), ZERO):.2f}"])
    return resp
