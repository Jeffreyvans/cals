import csv
from datetime import date as date_cls
from datetime import datetime
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import DecimalField, ExpressionWrapper, F, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from academics.models import Guardian, Student, balances_by_student
from accounts.decorators import role_required
from core.access import module_required, permission_required_code, user_has_code
from core.email_types import FEE_RECEIPT

from . import gateways, services
from .forms import (
    AccountForm,
    BudgetForm,
    CollectionTargetForm,
    DebitNoteForm,
    DiscountForm,
    ExchangeRateForm,
    ExpenseForm,
    FeeItemForm,
    FeeStructureForm,
    FinancialYearForm,
    GenerateInvoicesForm,
    InstalmentForm,
    InstalmentPlanForm,
    InvoiceForm,
    LatePenaltyRuleForm,
    PaymentForm,
    RefundForm,
    SponsorForm,
    SponsorshipForm,
    SupplierForm,
    TaxRateForm,
    WaiverForm,
)
from .models import (
    Account,
    BankStatementLine,
    Budget,
    CollectionTarget,
    DebitNote,
    Discount,
    DocumentType,
    ExchangeRate,
    Expense,
    FeeItem,
    FeeStructure,
    FinancialYear,
    InstalmentPlan,
    Invoice,
    JournalEntry,
    LatePenaltyRule,
    MoneyLog,
    Payment,
    Refund,
    Sponsor,
    Supplier,
    TaxRate,
    Waiver,
)

AGING_LABELS = ("Not yet due", "1–30 days overdue", "31–60 days overdue",
                "61+ days overdue")
ZERO = Decimal("0")
BAL_FIELD = DecimalField(max_digits=12, decimal_places=2)


def _parse_date(raw):
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


EXPORT_FORMATS = ("csv", "xlsx", "pdf")


def _export_format(request):
    fmt = (request.GET.get("format") or "csv").lower()
    return fmt if fmt in EXPORT_FORMATS else "csv"


def _export_response(request, filename, title, header, rows):
    """Download one report as CSV, Excel or PDF."""
    fmt = _export_format(request)
    stamp = date_cls.today().isoformat()
    if fmt == "xlsx":
        from io import BytesIO

        from openpyxl import Workbook
        from openpyxl.styles import Font

        book = Workbook()
        sheet = book.active
        sheet.title = title[:31]
        sheet.append([title])
        sheet["A1"].font = Font(bold=True, size=14)
        sheet.append(header)
        for cell in sheet[2]:
            cell.font = Font(bold=True)
        for row in rows:
            sheet.append([str(value) for value in row])
        for column in sheet.columns:
            width = max((len(str(cell.value or "")) for cell in column), default=8)
            sheet.column_dimensions[column[0].column_letter].width = min(width + 2, 50)
        buffer = BytesIO()
        book.save(buffer)
        response = HttpResponse(
            buffer.getvalue(),
            content_type=("application/vnd.openxmlformats-officedocument"
                          ".spreadsheetml.sheet"))
    elif fmt == "pdf":
        from io import BytesIO

        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

        buffer = BytesIO()
        page = landscape(A4) if len(header) > 5 else A4
        doc = SimpleDocTemplate(buffer, pagesize=page, leftMargin=12 * mm,
                                rightMargin=12 * mm, topMargin=12 * mm,
                                bottomMargin=12 * mm, title=title)
        styles = getSampleStyleSheet()
        data = [[str(cell) for cell in header]]
        data += [[str(cell) for cell in row] for row in rows]
        table = Table(data, repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#123524")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.white, colors.HexColor("#f5f5f5")]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        doc.build([Paragraph(title, styles["Title"]), Spacer(1, 4 * mm), table])
        response = HttpResponse(buffer.getvalue(),
                                content_type="application/pdf")
    else:
        response = HttpResponse(content_type="text/csv")
        response.write("\ufeff")
        writer = csv.writer(response)
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)
    response["Content-Disposition"] = (
        f'attachment; filename="{filename}-{stamp}.{fmt}"')
    return response


def _fiscal_for(instance, document_type):
    """The ZIMRA submission for a document, when the module is switched on.

    The lookup is done through the ``fiscalisation`` app only if it is present,
    so finance keeps working on an install without it.
    """
    try:
        from fiscalisation.models import FiscalSubmission
    except Exception:  # pragma: no cover - module not installed
        return None
    return FiscalSubmission.all_objects.filter(
        institution=instance.institution, document_type=document_type,
        document_id=instance.pk).first()


@module_required("finance")
@role_required("bursar")
def record_payment(request, pk):
    s = get_object_or_404(Student, pk=pk)
    form = PaymentForm(s, request.POST or None)
    if form.is_valid():
        p = form.save(commit=False)
        p.received_by = request.user
        p.save()
        messages.success(request, f"Payment received. Receipt {p.receipt_no}.")
        return redirect("receipt", pk=p.pk)

    return render(request, "finance/payment_form.html", {"s": s, "form": form})

@module_required("finance")
@role_required("bursar")
def create_invoice(request, pk):
    s = get_object_or_404(Student, pk=pk)
    form = InvoiceForm(request.POST or None)
    if form.is_valid():
        i = form.save(commit=False)
        i.student = s
        i.created_by = request.user
        i.save()
        messages.success(request, "Invoice added to the student's account.")
        return redirect("student_detail", pk=pk)

    return render(request, "finance/invoice_form.html", {"s": s, "form": form})

@module_required("finance")
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

@module_required("finance")
@role_required("bursar", "registrar")
def credit_note(request, pk):
    w = get_object_or_404(Waiver.objects.select_related("invoice__student__user",
                                                        "recorded_by"), pk=pk)
    return render(request, "finance/credit_note.html",
                  {"w": w, "s": w.invoice.student,
                   "fiscal": _fiscal_for(w, DocumentType.CREDIT_NOTE)})


@module_required("finance")
@role_required("bursar")
def debit_note(request, pk):
    """Raise a debit note: the permitted way to add to an issued invoice."""
    invoice = get_object_or_404(
        Invoice.objects.select_related("student__user"), pk=pk)
    form = DebitNoteForm(invoice, request.POST or None)
    if form.is_valid():
        note = form.save(commit=False)
        note.invoice = invoice
        note.recorded_by = request.user
        note.save()
        messages.success(request, f"Debit note {note.note_no} issued.")
        return redirect("debit_note_detail", pk=note.pk)
    return render(request, "finance/debit_note_form.html",
                  {"invoice": invoice, "form": form})


@module_required("finance")
@role_required("bursar", "registrar")
def debit_note_detail(request, pk):
    note = get_object_or_404(
        DebitNote.objects.select_related("invoice__student__user",
                                         "recorded_by"), pk=pk)
    return render(request, "finance/debit_note.html",
                  {"n": note, "s": note.invoice.student,
                   "fiscal": _fiscal_for(note, DocumentType.DEBIT_NOTE)})

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


@module_required("finance")
@role_required("bursar", "admin")
def audit_log(request):
    """Every money event, newest first."""
    entries, action, raw_from, raw_to = _audit_entries(request)
    return render(request, "finance/audit.html", {
        "entries": entries[:200], "action": action,
        "actions": MoneyLog.Action.choices,
        "raw_from": raw_from, "raw_to": raw_to,
        "export_query": (f"?action={action}&from={raw_from}"
                         f"&to={raw_to}&")})

def _receipt_payment(request, pk):
    """The payment a receipt view may show, or a 403 response for another student.

    Returns ``(payment, None)`` when the caller may see it, otherwise
    ``(None, response)``.
    """
    payment = get_object_or_404(
        Payment.objects.select_related("invoice__student__user",
                                       "invoice__institution", "received_by"),
        pk=pk)
    if request.user.role == "student" and payment.invoice.student.user != request.user:
        return None, render(request, "403.html", status=403)
    return payment, None


def _invoice_record(request, pk):
    """The invoice a document view may show, or a 403 for another student."""
    invoice = get_object_or_404(
        Invoice.objects.select_related("student__user", "institution"), pk=pk)
    if (request.user.role == "student"
            and invoice.student.user != request.user):
        return None, render(request, "403.html", status=403)
    return invoice, None


@module_required("finance")
@role_required("bursar", "registrar", "student")
def invoice_detail(request, pk):
    invoice, denied = _invoice_record(request, pk)
    if denied is not None:
        return denied
    return render(request, "finance/invoice.html", {
        "i": invoice,
        "s": invoice.student,
        "payments": invoice.payments.all().order_by("-paid_on"),
        "fiscal": _fiscal_for(invoice, DocumentType.INVOICE),
    })


@module_required("finance")
@role_required("bursar", "registrar", "student")
def invoice_pdf(request, pk):
    invoice, denied = _invoice_record(request, pk)
    if denied is not None:
        return denied
    from .invoices import build_invoice_pdf

    pdf = build_invoice_pdf(
        invoice, request=request,
        fiscal=_fiscal_for(invoice, DocumentType.INVOICE))
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="invoice-{invoice.number or invoice.pk}.pdf"')
    return response


@module_required("finance")
@role_required("bursar", "registrar", "student")
def receipt(request, pk):
    p, denied = _receipt_payment(request, pk)
    if denied is not None:
        return denied
    return render(request, "finance/receipt.html",
                  {"p": p, "s": p.invoice.student,
                   "fiscal": _fiscal_for(p, DocumentType.RECEIPT)})


@module_required("finance")
@role_required("bursar", "registrar", "student")
def receipt_pdf(request, pk):
    p, denied = _receipt_payment(request, pk)
    if denied is not None:
        return denied
    from .receipts import build_receipt_pdf

    pdf = build_receipt_pdf(p, request=request,
                            fiscal=_fiscal_for(p, DocumentType.RECEIPT))
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="receipt-{p.receipt_no or p.pk}.pdf"')
    return response


@module_required("finance")
@role_required("bursar", "registrar", "student")
def email_receipt(request, pk):
    """Email the student their receipt. A POST, so it is never sent by a link."""
    if request.method != "POST":
        return redirect("receipt", pk=pk)
    p, denied = _receipt_payment(request, pk)
    if denied is not None:
        return denied
    student_user = p.invoice.student.user
    if not student_user.email:
        messages.error(request, "This student has no email address on file.")
        return redirect("receipt", pk=p.pk)

    from core.document_header import header_context
    from core.logo import absolute_logo_url
    from core.notifications import absolute_url, queue_message, render_email_html, send_message

    institution = p.invoice.institution
    currency = p.currency or institution.currency
    url = reverse("verify_receipt", args=[p.verification_token])
    fiscal = _fiscal_for(p, DocumentType.RECEIPT)
    header = header_context(
        institution, layout=getattr(fiscal, "layout", None), request=request)
    html_body = render_email_html("emails/receipt.html", {
        "user": student_user, "institution": institution, "payment": p,
        "student": p.invoice.student, "currency": currency,
        "verify_url": absolute_url(url),
        "header": header,
        "logo_url": header["logo_url"] or absolute_logo_url(institution, request, 160)})
    body = (
        f"Hello {student_user.get_full_name() or student_user.username},\n\n"
        f"We received your payment of {currency}{p.amount} "
        f"for {p.invoice.description}.\n"
        f"Receipt number: {p.receipt_no}\n\n"
        f"Verify this receipt: {absolute_url(url)}\n"
    )
    message = queue_message(
        "email", student_user.email, body,
        subject=f"Payment receipt {p.receipt_no} from {institution.name}",
        institution=institution, related_object=p, html_body=html_body,
        email_type=FEE_RECEIPT)
    send_message(message)
    messages.success(request, f"Receipt {p.receipt_no} emailed to {student_user.email}.")
    return redirect("receipt", pk=p.pk)


# ---------- reports ----------
def _debtors_data(show, request=None):
    """Rows (students annotated with amounts) and totals for the given filter."""
    from academics.hierarchy import filter_hierarchy

    billed, paid, waived = balances_by_student()
    students = Student.objects.select_related("user", "programme", "school_class") \
        .filter(pk__in=billed.keys())
    if request is not None:
        students = filter_hierarchy(
            students, request, school_field="school", department_field="department",
            level_field="school_class__grade_level", class_field="school_class")
    rows = []
    for s in students.order_by("user__last_name", "user__first_name"):
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


@module_required("finance")
@role_required("bursar", "registrar")
def report_debtors(request):
    """Who owes what, how overdue it is, printable for the office wall."""
    show = _clean_show(request.GET.get("show", "owing"))
    rows, totals = _debtors_data(show, request)
    return render(request, "finance/debtors.html", {
        "rows": rows, "show": show, "totals": totals,
        "aging": _aging_data(),
        "export_query": ("?" + request.GET.urlencode() + "&") if request.GET else "?"})


@module_required("finance")
@role_required("bursar", "registrar")
def export_debtors(request):
    show = _clean_show(request.GET.get("show", "owing"))
    rows, totals = _debtors_data(show, request)
    data = [[s.name, s.student_no, s.programme.name,
             s.class_label if s.school_class_id else "",
             f"{s.billed_amt:.2f}", f"{s.paid_amt:.2f}",
             f"{s.waived_amt:.2f}", f"{s.owed:.2f}"] for s in rows]
    data.append(["TOTAL", "", "", "", f"{totals['billed']:.2f}",
                 f"{totals['paid']:.2f}", f"{totals['waived']:.2f}",
                 f"{totals['owed']:.2f}"])
    return _export_response(
        request, "debtors", "Debtors",
        ["Student", "Student no", "Programme", "Class", "Billed", "Received",
         "Waived", "Owed"], data)


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


def _collections_data(date_from, date_to, request=None):
    from academics.hierarchy import filter_hierarchy

    payments = Payment.objects.select_related("invoice__student__user",
                                              "received_by")
    if request is not None:
        payments = filter_hierarchy(
            payments, request, school_field="invoice__student__school",
            department_field="invoice__student__department",
            level_field="invoice__student__school_class__grade_level",
            class_field="invoice__student__school_class")
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


@module_required("finance")
@role_required("bursar", "registrar")
def report_collections(request):
    """Money received in a period, broken down by payment method."""
    date_from, date_to, raw_from, raw_to = _date_filters(request)
    payments, total, student_count, methods = _collections_data(date_from,
                                                                date_to, request)
    return render(request, "finance/collections.html", {
        "payments": payments, "total": total, "student_count": student_count,
        "methods": methods, "raw_from": raw_from, "raw_to": raw_to,
        "export_query": ("?" + request.GET.urlencode() + "&") if request.GET else "?"})


@module_required("finance")
@role_required("bursar", "registrar")
def export_collections(request):
    date_from, date_to, _, _ = _date_filters(request)
    payments, total, _, methods = _collections_data(date_from, date_to, request)
    data = [[p.receipt_no, p.paid_on.strftime("%Y-%m-%d %H:%M"),
             p.invoice.student.name, p.invoice.student.student_no,
             p.invoice.description, p.get_method_display(),
             p.reference or "", f"{p.amount:.2f}", p.received_by_name]
            for p in payments]
    data.append([])
    data.append(["By method"])
    for label, amount in methods:
        data.append([label, "", "", "", "", "", "", f"{amount:.2f}"])
    data.append(["TOTAL", "", "", "", "", "", "", f"{total:.2f}"])
    return _export_response(
        request, "collections", "Collections",
        ["Receipt", "Date", "Student", "Student no", "For", "Method",
         "Reference", "Amount", "Received by"], data)


@module_required("finance")
@permission_required_code("finance.view_reports")
def collection_targets(request):
    """How each period is tracking against its collections goal."""
    return render(request, "finance/collection_targets.html", {
        "rows": services.collection_target_rows()})


@module_required("finance")
@permission_required_code("finance.manage_fee_structures")
def collection_target_create(request):
    form = CollectionTargetForm(request.POST or None)
    if form.is_valid():
        target = form.save(commit=False)
        target.created_by = request.user
        target.save()
        messages.success(request, "Collection target saved.")
        return redirect("collection_targets")
    return render(request, "finance/form.html", {
        "form": form, "title": "New collection target",
        "back": "collection_targets"})


@module_required("finance")
@permission_required_code("finance.manage_fee_structures")
def collection_target_delete(request, pk):
    target = get_object_or_404(CollectionTarget, pk=pk)
    if request.method == "POST":
        target.delete()
        messages.success(request, "Collection target removed.")
    return redirect("collection_targets")


@module_required("finance")
@permission_required_code("finance.view_reports")
def export_collection_targets(request):
    rows = services.collection_target_rows()
    return _export_response(
        request, "collection-targets", "Collection vs target",
        ["Target", "Department", "Year", "From", "To", "Goal",
         "Collected", "Variance", "Percent"],
        [[r["target"].name,
          r["target"].department.name if r["target"].department else "",
          str(r["target"].financial_year) if r["target"].financial_year else "",
          r["target"].start_date.isoformat(),
          r["target"].end_date.isoformat(),
          f"{r['target'].amount:.2f}", f"{r['collected']:.2f}",
          f"{r['variance']:.2f}", f"{r['percent']:.1f}"] for r in rows])


@module_required("finance")
@role_required("bursar", "admin")
def export_audit(request):
    entries, _, _, _ = _audit_entries(request)
    entries = list(entries)
    data = [[e.created.strftime("%Y-%m-%d %H:%M"), e.actor_name,
             e.get_action_display(), e.description,
             e.student.name if e.student else "",
             e.student.student_no if e.student else "",
             e.reference, f"{e.amount:.2f}"] for e in entries]
    data.append(["TOTAL", "", "", "", "", "", "",
                 f"{sum((e.amount for e in entries), ZERO):.2f}"])
    return _export_response(
        request, "audit-log", "Audit log",
        ["When", "Who", "Action", "Details", "Student", "Student no",
         "Reference", "Amount"], data)

# ---------- fee structures and automatic invoicing ----------


@module_required("finance")
@permission_required_code("finance.manage_fee_structures")
def fee_structures(request):
    from academics.hierarchy import filter_hierarchy

    structures = FeeStructure.objects.prefetch_related("items")
    structures = filter_hierarchy(
        structures, request, school_field="programme__school",
        department_field="programme__department",
        level_field="school_class__grade_level", class_field="school_class")
    return render(request, "finance/fee_structures.html", {"structures": structures})


@module_required("finance")
@permission_required_code("finance.manage_fee_structures")
def fee_structure_create(request):
    form = FeeStructureForm(request.POST or None)
    if form.is_valid():
        structure = form.save()
        messages.success(request, f"Fee structure {structure.name} created.")
        return redirect("fee_structure_detail", pk=structure.pk)
    return render(request, "finance/form.html", {
        "form": form, "title": "New fee structure",
        "back": "fee_structures"})


@module_required("finance")
@permission_required_code("finance.manage_fee_structures")
def fee_structure_detail(request, pk):
    structure = get_object_or_404(FeeStructure, pk=pk)
    item_form = FeeItemForm(request.POST or None)
    if request.method == "POST":
        if item_form.is_valid():
            item = item_form.save(commit=False)
            item.structure = structure
            item.save()
            messages.success(request, f"{item.name} added.")
            return redirect("fee_structure_detail", pk=pk)
    return render(request, "finance/fee_structure_detail.html", {
        "structure": structure, "items": structure.items.all(), "form": item_form})


@module_required("finance")
@permission_required_code("finance.manage_fee_structures")
def fee_item_delete(request, pk):
    item = get_object_or_404(FeeItem, pk=pk)
    structure_pk = item.structure_id
    if request.method == "POST":
        item.delete()
        messages.success(request, "Fee item removed.")
    return redirect("fee_structure_detail", pk=structure_pk)


@module_required("finance")
@permission_required_code("finance.issue_invoice")
def generate_invoices(request):
    """Preview then create invoices for every eligible student."""
    form = GenerateInvoicesForm(request.POST or None)
    result = None
    if request.method == "POST" and form.is_valid():
        structure = form.cleaned_data["structure"]
        term = form.cleaned_data["term"]
        confirm = request.POST.get("action") == "confirm"
        result = services.generate_invoices(
            structure, term, preview=not confirm, created_by=request.user)
        if confirm:
            messages.success(
                request, f"{result['created']} invoices created.")
    return render(request, "finance/generate_invoices.html", {
        "form": form, "result": result})


# ---------- chart of accounts, journal and financial year ----------


@module_required("finance")
@permission_required_code("finance.view_ledger")
def accounts(request):
    services.ensure_chart_of_accounts(request.institution)
    rows = Account.objects.all()
    return render(request, "finance/accounts.html", {"rows": rows})


@module_required("finance")
@permission_required_code("finance.view_ledger")
def account_create(request):
    form = AccountForm(request.POST or None)
    if form.is_valid():
        form.save()
        messages.success(request, "Account added.")
        return redirect("accounts")
    return render(request, "finance/form.html", {
        "form": form, "title": "New account", "back": "accounts"})


@module_required("finance")
@permission_required_code("finance.view_ledger")
def journal_list(request):
    entries = JournalEntry.objects.prefetch_related("lines__account")
    return render(request, "finance/journal.html", {"entries": entries[:300]})


@module_required("finance")
@permission_required_code("finance.view_ledger")
def journal_detail(request, pk):
    entry = get_object_or_404(JournalEntry, pk=pk)
    reversal = JournalEntry.objects.filter(
        source_type="Reversal", source_id=str(entry.pk)).first()
    return render(request, "finance/journal_detail.html", {
        "entry": entry, "lines": entry.lines.select_related("account"),
        "reversal": reversal})


@module_required("finance")
@role_required("bursar", "admin")
@permission_required_code("finance.view_ledger")
def journal_reverse(request, pk):
    entry = get_object_or_404(JournalEntry, pk=pk)
    if request.method == "POST":
        reason = request.POST.get("reason", "")
        try:
            services.reverse_journal_entry(entry, reason, actor=request.user)
        except ValidationError as exc:
            messages.error(request, exc.messages[0] if exc.messages else str(exc))
        else:
            messages.success(request, "Reversal posted.")
    return redirect("journal_detail", pk=entry.pk)


@module_required("finance")
@permission_required_code("finance.view_ledger")
def financial_years(request):
    rows = FinancialYear.objects.all()
    form = FinancialYearForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Financial year added.")
        return redirect("financial_years")
    return render(request, "finance/financial_years.html", {
        "rows": rows, "form": form})


@module_required("finance")
@permission_required_code("finance.view_ledger")
def financial_year_close(request, pk):
    year = get_object_or_404(FinancialYear, pk=pk)
    if request.method == "POST":
        if year.is_closed:
            services.open_financial_year(year)
            messages.success(request, f"{year.name} reopened.")
        else:
            services.close_financial_year(year, request.user)
            messages.success(request, f"{year.name} closed.")
    return redirect("financial_years")


@module_required("finance")
@permission_required_code("finance.view_reports")
def trial_balance(request):
    return render(request, "finance/trial_balance.html",
                  services.trial_balance(request.institution))


@module_required("finance")
@permission_required_code("finance.view_reports")
def income_statement(request):
    start, end, raw_from, raw_to = _date_filters(request)
    data = services.income_statement(start, end)
    data.update({"raw_from": raw_from, "raw_to": raw_to,
                 "export_query": ("?" + request.GET.urlencode() + "&") if request.GET else "?"})
    return render(request, "finance/income_statement.html", data)


@module_required("finance")
@permission_required_code("finance.view_reports")
def balance_sheet(request):
    return render(request, "finance/balance_sheet.html",
                  services.balance_sheet())


# ---------- discounts, sponsors and instalments ----------


@module_required("finance")
@permission_required_code("finance.manage_fee_structures")
def discount_list(request):
    rows = Discount.objects.select_related("student__user", "sponsor", "invoice")
    return render(request, "finance/discounts.html", {"rows": rows})


@module_required("finance")
@permission_required_code("finance.manage_fee_structures")
def discount_create(request):
    form = DiscountForm(request.POST or None)
    if form.is_valid():
        discount = form.save(commit=False)
        discount.created_by = request.user
        discount.save()
        if discount.invoice_id:
            services.apply_discount(discount)
        messages.success(request, "Discount saved.")
        return redirect("discount_list")
    return render(request, "finance/form.html", {
        "form": form, "title": "New discount or bursary",
        "back": "discount_list"})


@module_required("finance")
@permission_required_code("finance.manage_fee_structures")
def sponsor_list(request):
    rows = Sponsor.objects.all()
    return render(request, "finance/sponsors.html", {"rows": rows})


@module_required("finance")
@permission_required_code("finance.manage_fee_structures")
def sponsor_create(request):
    form = SponsorForm(request.POST or None)
    if form.is_valid():
        form.save()
        messages.success(request, "Sponsor added.")
        return redirect("sponsor_list")
    return render(request, "finance/form.html", {
        "form": form, "title": "New sponsor", "back": "sponsor_list"})


@module_required("finance")
@permission_required_code("finance.manage_fee_structures")
def sponsor_detail(request, pk):
    sponsor = get_object_or_404(Sponsor, pk=pk)
    form = SponsorshipForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        link = form.save(commit=False)
        link.sponsor = sponsor
        link.save()
        messages.success(request, "Student linked to the sponsor.")
        return redirect("sponsor_detail", pk=pk)
    return render(request, "finance/sponsor_detail.html", {
        "sponsor": sponsor,
        "links": sponsor.sponsorships.select_related("student__user"),
        "form": form,
        "statement": services.sponsor_statement(sponsor)})


@module_required("finance")
@permission_required_code("finance.view_reports")
def sponsor_statement(request, pk):
    sponsor = get_object_or_404(Sponsor, pk=pk)
    start, end, _, _ = _date_filters(request)
    return render(request, "finance/sponsor_statement.html", {
        "sponsor": sponsor,
        **services.sponsor_statement(sponsor, start, end)})


@module_required("finance")
@permission_required_code("finance.manage_fee_structures")
def instalment_plans(request):
    rows = InstalmentPlan.objects.select_related("student__user", "term")
    return render(request, "finance/instalment_plans.html", {"rows": rows})


@module_required("finance")
@permission_required_code("finance.manage_fee_structures")
def instalment_plan_create(request):
    form = InstalmentPlanForm(request.POST or None)
    if form.is_valid():
        plan = form.save(commit=False)
        plan.created_by = request.user
        plan.save()
        messages.success(request, "Instalment plan created.")
        return redirect("instalment_plan_detail", pk=plan.pk)
    return render(request, "finance/form.html", {
        "form": form, "title": "New instalment plan",
        "back": "instalment_plans"})


@module_required("finance")
@permission_required_code("finance.manage_fee_structures")
def instalment_plan_detail(request, pk):
    plan = get_object_or_404(InstalmentPlan, pk=pk)
    form = InstalmentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        instalment = form.save(commit=False)
        instalment.plan = plan
        instalment.save()
        messages.success(request, "Instalment added.")
        return redirect("instalment_plan_detail", pk=pk)
    return render(request, "finance/instalment_plan_detail.html", {
        "plan": plan, "instalments": plan.instalments.all(), "form": form})


@module_required("finance")
@permission_required_code("finance.view_reports")
def overdue_instalments(request):
    rows = services.overdue_instalments()
    return render(request, "finance/overdue_instalments.html", {"rows": rows})


@module_required("finance")
@permission_required_code("finance.manage_fee_structures")
def penalty_rules(request):
    rows = LatePenaltyRule.objects.all()
    form = LatePenaltyRuleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Penalty rule saved.")
        return redirect("penalty_rules")
    return render(request, "finance/penalty_rules.html", {
        "rows": rows, "form": form})


@module_required("finance")
@permission_required_code("finance.manage_fee_structures")
def apply_penalties(request, pk):
    rule = get_object_or_404(LatePenaltyRule, pk=pk)
    if request.method == "POST":
        made = services.apply_late_penalties(rule, created_by=request.user)
        messages.success(request, f"{len(made)} penalties applied.")
    return redirect("penalty_rules")


# ---------- refunds ----------


@module_required("finance")
@permission_required_code("finance.approve_refund")
def refund_list(request):
    rows = Refund.objects.select_related("payment__invoice__student__user",
                                         "requested_by", "approved_by")
    return render(request, "finance/refunds.html", {"rows": rows})


@module_required("finance")
@permission_required_code("finance.receive_payment")
def refund_request(request):
    form = RefundForm(request.POST or None)
    if form.is_valid():
        refund = form.save(commit=False)
        refund.requested_by = request.user
        refund.save()
        messages.success(request, "Refund sent for approval.")
        return redirect("refund_list")
    return render(request, "finance/form.html", {
        "form": form, "title": "Request a refund", "back": "refund_list"})


@module_required("finance")
@permission_required_code("finance.approve_refund")
def refund_decide(request, pk):
    refund = get_object_or_404(Refund, pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "approve":
            services.approve_refund(refund, request.user)
            messages.success(request, "Refund approved and posted.")
        elif action == "reject":
            services.reject_refund(refund, request.user)
            messages.success(request, "Refund rejected.")
    return redirect("refund_list")


# ---------- suppliers, expenses and budgets ----------


@module_required("finance")
@permission_required_code("finance.view_reports")
def supplier_list(request):
    rows = Supplier.objects.all()
    return render(request, "finance/suppliers.html", {"rows": rows})


@module_required("finance")
@permission_required_code("finance.manage_fee_structures")
def supplier_create(request):
    form = SupplierForm(request.POST or None)
    if form.is_valid():
        form.save()
        messages.success(request, "Supplier added.")
        return redirect("supplier_list")
    return render(request, "finance/form.html", {
        "form": form, "title": "New supplier", "back": "supplier_list"})


@module_required("finance")
@permission_required_code("finance.view_reports")
def expense_list(request):
    rows = Expense.objects.select_related("supplier", "department")
    return render(request, "finance/expenses.html", {"rows": rows})


@module_required("finance")
@permission_required_code("finance.manage_fee_structures")
def expense_create(request):
    form = ExpenseForm(request.POST or None)
    if form.is_valid():
        expense = form.save(commit=False)
        expense.created_by = request.user
        expense.save()
        messages.success(request, "Expense recorded.")
        return redirect("expense_list")
    return render(request, "finance/form.html", {
        "form": form, "title": "New expense", "back": "expense_list"})


@module_required("finance")
@permission_required_code("finance.view_reports")
def budget_list(request):
    rows = Budget.objects.select_related("department", "financial_year")
    return render(request, "finance/budgets.html", {
        "rows": rows,
        "variances": [services.budget_variance(b) for b in rows]})


@module_required("finance")
@permission_required_code("finance.manage_fee_structures")
def budget_create(request):
    form = BudgetForm(request.POST or None)
    if form.is_valid():
        budget = form.save(commit=False)
        budget.created_by = request.user
        budget.save()
        messages.success(request, "Budget saved.")
        return redirect("budget_list")
    return render(request, "finance/form.html", {
        "form": form, "title": "New budget", "back": "budget_list"})


# ---------- reports, statements and reconciliation ----------


@module_required("finance")
@permission_required_code("finance.view_reports")
def report_income_by_item(request):
    start, end, raw_from, raw_to = _date_filters(request)
    return render(request, "finance/income_by_item.html", {
        "rows": services.income_by_fee_item(start, end),
        "raw_from": raw_from, "raw_to": raw_to,
        "export_query": ("?" + request.GET.urlencode() + "&") if request.GET else "?"})


@module_required("finance")
@permission_required_code("finance.view_reports")
def report_refunds(request):
    rows = Refund.objects.select_related("payment__invoice__student__user")
    return render(request, "finance/refunds_report.html", {"rows": rows})


@module_required("finance")
@permission_required_code("finance.view_reports")
def report_expenses(request):
    rows = Expense.objects.select_related("supplier", "department")
    return render(request, "finance/expenses_report.html", {
        "rows": rows, "total": sum((r.amount for r in rows), ZERO)})


@module_required("finance")
@permission_required_code("finance.view_reports")
def report_cashup(request):
    day = _parse_date(request.GET.get("date", "")) or timezone.localdate()
    payments = Payment.objects.filter(paid_on__date=day).select_related(
        "invoice__student__user", "received_by")
    by_method = {}
    by_user = {}
    for payment in payments:
        by_method[payment.get_method_display()] = \
            by_method.get(payment.get_method_display(), ZERO) + payment.amount
        by_user[payment.received_by_name] = \
            by_user.get(payment.received_by_name, ZERO) + payment.amount
    return render(request, "finance/cashup.html", {
        "day": day, "payments": payments, "total": sum(by_method.values(), ZERO),
        "by_method": by_method.items(), "by_user": by_user.items(),
        "export_query": f"?date={day.isoformat()}&"})


@module_required("finance")
@permission_required_code("finance.view_reports")
def export_trial_balance(request):
    data = services.trial_balance(request.institution)
    rows = [[r["account"].code, r["account"].name, f"{r['debit']:.2f}",
             f"{r['credit']:.2f}", f"{r['balance']:.2f}"]
            for r in data["rows"]]
    rows.append(["TOTAL", "", f"{data['total_debit']:.2f}",
                 f"{data['total_credit']:.2f}", ""])
    return _export_response(
        request, "trial-balance", "Trial balance",
        ["Code", "Account", "Debit", "Credit", "Balance"], rows)


@module_required("finance")
@permission_required_code("finance.view_reports")
def export_income_statement(request):
    start, end, _, _ = _date_filters(request)
    data = services.income_statement(start, end)
    rows = [["Income", a.code, a.name, f"{amount:.2f}"]
            for a, amount in data["income"]]
    rows.append(["Income", "", "Total income", f"{data['total_income']:.2f}"])
    rows += [["Expenses", a.code, a.name, f"{amount:.2f}"]
             for a, amount in data["expenses"]]
    rows.append(["Expenses", "", "Total expenses",
                 f"{data['total_expenses']:.2f}"])
    rows.append(["Net", "", "Net result", f"{data['net']:.2f}"])
    return _export_response(
        request, "income-statement", "Income statement",
        ["Section", "Code", "Account", "Amount"], rows)


@module_required("finance")
@permission_required_code("finance.view_reports")
def export_balance_sheet(request):
    data = services.balance_sheet()
    rows = [["Assets", a.code, a.name, f"{amount:.2f}"]
            for a, amount in data["assets"]]
    rows.append(["Assets", "", "Total assets", f"{data['total_assets']:.2f}"])
    rows += [["Liabilities", a.code, a.name, f"{amount:.2f}"]
             for a, amount in data["liabilities"]]
    rows.append(["Liabilities", "", "Total liabilities",
                 f"{data['total_liabilities']:.2f}"])
    rows += [["Equity", a.code, a.name, f"{amount:.2f}"]
             for a, amount in data["equity"]]
    rows.append(["Equity", "", "Total equity", f"{data['total_equity']:.2f}"])
    return _export_response(
        request, "balance-sheet", "Balance sheet",
        ["Section", "Code", "Account", "Amount"], rows)


@module_required("finance")
@permission_required_code("finance.view_reports")
def export_income_by_item(request):
    start, end, _, _ = _date_filters(request)
    rows = services.income_by_fee_item(start, end)
    return _export_response(
        request, "income-by-item", "Income by fee item",
        ["Fee item", "Invoiced"],
        [[label, f"{amount:.2f}"] for label, amount in rows]
        + [["TOTAL", f"{sum((a for _, a in rows), ZERO):.2f}"]])


@module_required("finance")
@permission_required_code("finance.view_reports")
def export_refunds(request):
    rows = Refund.objects.select_related("payment__invoice__student__user")
    return _export_response(
        request, "refunds-report", "Refunds report",
        ["Receipt", "Student", "Amount", "Status", "Requested"],
        [[r.payment.receipt_no, r.payment.invoice.student.name,
          f"{r.amount:.2f}", r.get_status_display(),
          r.created.strftime("%Y-%m-%d")] for r in rows])


@module_required("finance")
@permission_required_code("finance.view_reports")
def export_expenses(request):
    rows = Expense.objects.select_related("supplier", "department")
    data = [[e.date.isoformat(), e.description, e.category,
             e.supplier.name if e.supplier else "", f"{e.amount:.2f}"]
            for e in rows]
    data.append(["TOTAL", "", "", "",
                 f"{sum((e.amount for e in rows), ZERO):.2f}"])
    return _export_response(
        request, "expenses-report", "Expenses report",
        ["Date", "Description", "Category", "Supplier", "Amount"], data)


@module_required("finance")
@permission_required_code("finance.view_reports")
def export_cashup(request):
    day = _parse_date(request.GET.get("date", "")) or timezone.localdate()
    payments = Payment.objects.filter(paid_on__date=day).select_related(
        "invoice__student__user", "received_by")
    rows = [[p.receipt_no, p.paid_on.strftime("%Y-%m-%d %H:%M"),
             p.invoice.student.name, p.get_method_display(),
             f"{p.amount:.2f}", p.received_by_name] for p in payments]
    rows.append(["TOTAL", "", "", "", f"{sum((p.amount for p in payments), ZERO):.2f}",
                 ""])
    return _export_response(
        request, f"cashup-{day.isoformat()}", f"Daily cash-up {day}",
        ["Receipt", "Date", "Student", "Method", "Amount", "Received by"], rows)


@module_required("finance")
@permission_required_code("finance.view_reports")
def export_budgets(request):
    rows = Budget.objects.select_related("department", "financial_year")
    data = []
    for budget in rows:
        variance = services.budget_variance(budget)
        data.append([
            budget.name,
            budget.department.name if budget.department else "All",
            budget.financial_year.name if budget.financial_year else "",
            f"{budget.amount:.2f}", f"{variance['spent']:.2f}",
            f"{variance['variance']:.2f}"])
    return _export_response(
        request, "budgets", "Budgets",
        ["Name", "Department", "Year", "Budget", "Spent", "Variance"], data)


@module_required("finance")
@permission_required_code("finance.view_reports")
def vat_summary(request):
    """Output VAT charged against input VAT on expenses for a period."""
    start, end, raw_from, raw_to = _date_filters(request)
    data = services.vat_summary(start, end)
    data.update({
        "raw_from": raw_from, "raw_to": raw_to,
        "institution": request.institution,
        "export_query": f"?from={raw_from}&to={raw_to}&",
    })
    return render(request, "finance/vat_summary.html", data)


@module_required("finance")
@permission_required_code("finance.view_reports")
def export_vat_summary(request):
    start, end, _, _ = _date_filters(request)
    data = services.vat_summary(start, end)
    rows = [["Output", r["document"], r["date"], r["party"],
             f"{r['net']:.2f}", f"{r['rate']:.2f}", f"{r['vat']:.2f}"]
            for r in data["output_rows"]]
    rows.append(["Output", "", "", "Total output VAT", "", "",
                 f"{data['output_vat']:.2f}"])
    rows += [["Input", r["document"], r["date"], r["party"],
              f"{r['net']:.2f}", f"{r['rate']:.2f}", f"{r['vat']:.2f}"]
             for r in data["input_rows"]]
    rows.append(["Input", "", "", "Total input VAT", "", "",
                 f"{data['input_vat']:.2f}"])
    rows.append(["Net", "", "", "VAT payable", "", "",
                 f"{data['net_vat']:.2f}"])
    return _export_response(
        request, "vat-summary", "VAT summary",
        ["Direction", "Document", "Date", "Party", "Net", "Rate", "VAT"], rows)


@module_required("finance")
@permission_required_code("finance.manage_tax")
def tax_rates(request):
    """The dated VAT rates. The rate in force on a date is used that day."""
    rows = TaxRate.objects.all()
    rate_form = TaxRateForm(request.POST or None, prefix="rate")
    fx_form = ExchangeRateForm(request.POST or None, prefix="fx")
    if request.method == "POST":
        if "save_rate" in request.POST and rate_form.is_valid():
            rate_form.save()
            messages.success(request, "Tax rate saved.")
            return redirect("tax_rates")
        if "save_fx" in request.POST and fx_form.is_valid():
            fx_form.save()
            messages.success(request, "Exchange rate saved.")
            return redirect("tax_rates")
    return render(request, "finance/tax_rates.html", {
        "rows": rows, "rate_form": rate_form, "fx_form": fx_form,
        "exchange_rates": ExchangeRate.objects.all(),
        "institution": request.institution})


@module_required("finance")
@permission_required_code("finance.view_reports")
def student_statement(request, pk):
    student = get_object_or_404(Student, pk=pk)
    invoices = student.invoices.prefetch_related("payments", "waivers", "discounts")
    return render(request, "finance/statement.html", {
        "s": student, "invoices": invoices,
        "balance": services.student_balance(student),
        "credit": services.credit_balance(student)})


@module_required("finance")
@permission_required_code("finance.view_reports")
def student_statement_pdf(request, pk):
    from .statements import build_statement_pdf

    student = get_object_or_404(Student, pk=pk)
    pdf = build_statement_pdf(student)
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="statement-{student.student_no or student.pk}.pdf"')
    return response


@module_required("finance")
@permission_required_code("finance.view_reports")
def bank_lines(request):
    rows = BankStatementLine.objects.select_related("matched_payment")
    return render(request, "finance/bank_lines.html", {
        "rows": rows,
        "unmatched": rows.filter(matched_payment__isnull=True).count()})


@module_required("finance")
@permission_required_code("finance.manage_fee_structures")
def bank_import(request):
    if request.method == "POST":
        upload = request.FILES.get("file")
        if upload is None:
            messages.error(request, "Choose a CSV file to import.")
        else:
            text = upload.read().decode("utf-8-sig", errors="replace")
            made = services.import_bank_statement(
                text, created_by=request.user, institution=request.institution)
            messages.success(request, f"{len(made)} statement lines imported.")
            return redirect("bank_lines")
    return render(request, "finance/bank_import.html")


@module_required("finance")
@permission_required_code("finance.manage_fee_structures")
def bank_match(request):
    if request.method == "POST":
        matched = services.match_bank_lines()
        messages.success(request, f"{matched} statement lines matched.")
    return redirect("bank_lines")


# ---------- public receipt verification and online payment ----------


def verify_receipt(request, token):
    payment = get_object_or_404(
        Payment.all_objects.select_related("invoice__student__user",
                                           "invoice__institution"),
        verification_token=token)
    return render(request, "finance/verify_receipt.html", {
        "p": payment, "s": payment.invoice.student,
        "INSTITUTION": payment.invoice.institution.name})


def _can_pay_invoice(user, invoice):
    """Staff with the payment code, the student, or a finance-visible parent."""
    if user_has_code(user, "finance.receive_payment"):
        return True
    student = Student.objects.filter(user=user).first()
    if student is not None and student.pk == invoice.student_id:
        return True
    if getattr(user, "role", "") == "parent":
        link = Guardian.objects.filter(
            parent=user, student=invoice.student).first()
        return bool(
            link is not None
            and link.can_view_finance
            and user_has_code(user, "parent_portal.view_finance"))
    return False


@module_required("finance")
@login_required
def pay_online(request, pk):
    from core.idempotency import begin, finish, new_key

    invoice = get_object_or_404(Invoice, pk=pk)
    if not _can_pay_invoice(request.user, invoice):
        raise PermissionDenied("You cannot pay this invoice.")
    if request.method == "POST":
        amount = request.POST.get("amount") or invoice.balance
        key, previous = begin(request, f"payment:{invoice.pk}")
        if previous and previous.get("redirect_url"):
            return redirect(previous["redirect_url"])
        try:
            _transaction, redirect_url = gateways.start_online_payment(
                invoice, Decimal(str(amount)), key,
                return_url=request.build_absolute_uri("/finance/paynow/return/"),
                result_url=request.build_absolute_uri("/finance/paynow/webhook/"))
        except gateways.GatewayError:
            from core.errors import add_error_message

            add_error_message(request, "EXT-PAY")
        else:
            finish(key, {"redirect_url": redirect_url})
            return redirect(redirect_url)
    key = request.POST.get("idempotency_key") or new_key()
    return render(
        request, "finance/pay_online.html",
        {"invoice": invoice, "idempotency_key": key},
    )


def paynow_return(request):
    reference = request.GET.get("reference", "")
    from .models import GatewayTransaction

    transaction = GatewayTransaction.objects.filter(reference=reference).first()
    if transaction is not None:
        gateways.get_gateway("paynow").poll(transaction)
    messages.success(request, "Thank you. Your payment status is being checked.")
    if getattr(request.user, "role", "") == "parent":
        return redirect("parent_portal")
    return redirect("student_portal")


@csrf_exempt
def paynow_webhook(request):
    """Provider callback: the Paynow signature is the authentication, not a CSRF token."""
    from django.http import JsonResponse

    from core.errors import error_json

    if request.method != "POST":
        return error_json(request, "INPUT-INVALID", 405)
    try:
        gateways.get_gateway("paynow").handle_webhook(
            request.POST, request.POST.get("hash", ""))
    except gateways.GatewayError:
        # Never echo a provider message: it may contain keys or account data.
        return error_json(request, "EXT-PAY", 502)
    return JsonResponse({"status": "ok"})
