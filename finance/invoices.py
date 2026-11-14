"""PDF invoices.

An invoice is a tax document, so it carries the same masthead as a receipt:
logo or initials, name, address, contacts and the VAT/BP numbers when the
institution has them. The lines show the gross amount, the VAT inside it and
what is still owed, which keeps the document readable in black and white and
usable by an accountant.
"""

import html
from io import BytesIO

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from core.branding import primary_colour
from core.document_header import build_header

INK = colors.HexColor("#0f172a")
MUTED = colors.HexColor("#64748b")
RULE = colors.HexColor("#e2e8f0")


def _safe(value):
    return html.escape(str(value if value is not None else ""))


def _styles(primary):
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="InvoiceTitle", parent=styles["Title"], fontName="Helvetica-Bold",
        fontSize=20, leading=24, textColor=colors.HexColor(primary), spaceAfter=2))
    styles.add(ParagraphStyle(
        name="InvoiceH2", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=12, leading=15, textColor=colors.HexColor(primary),
        spaceBefore=6, spaceAfter=4))
    styles.add(ParagraphStyle(
        name="InvoiceSmall", parent=styles["BodyText"], fontName="Helvetica",
        fontSize=8, leading=11, textColor=MUTED))
    styles.add(ParagraphStyle(
        name="InvoiceCell", parent=styles["BodyText"], fontName="Helvetica",
        fontSize=9, leading=12, textColor=INK))
    return styles


def build_invoice_pdf(invoice, request=None, fiscal=None):
    """PDF bytes for one invoice."""
    student = invoice.student
    institution = student.institution
    currency = invoice.currency or institution.currency
    primary = primary_colour(institution)
    styles = _styles(primary)
    header = getattr(fiscal, "layout", None) if fiscal is not None else None

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=1.8 * cm, bottomMargin=1.8 * cm,
        title=f"Invoice {invoice.number}", author=institution.name)

    story = [
        build_header(
            institution, "Tax invoice",
            subtitle=f"{invoice.number} · {invoice.created:%d %B %Y}",
            title_style=styles["InvoiceTitle"],
            small_style=styles["InvoiceSmall"],
            header=header, logo_size=1.8 * cm,
            widths=(2.2 * cm, 10.0 * cm, 5.4 * cm), primary=primary),
        Spacer(1, 0.45 * cm),
    ]

    rows = [
        ["Billed to", f"{student.name} ({student.student_no or '-'})"],
        ["Description", invoice.description],
        ["Due date", invoice.due_date.strftime("%d %B %Y")],
        ["Status", "Paid" if invoice.balance <= 0 else "Outstanding"],
        ["Amount", f"{currency}{invoice.amount:.2f}"],
        ["VAT included", f"{currency}{invoice.tax_amount:.2f} "
                         f"({invoice.tax_rate:g}%)"],
        ["Paid", f"{currency}{invoice.paid:.2f}"],
    ]
    if invoice.waived:
        rows.append(["Waived / credited", f"{currency}{invoice.waived:.2f}"])
    if invoice.debited:
        rows.append(["Debit notes", f"{currency}{invoice.debited:.2f}"])
    rows.append(["Balance due", f"{currency}{invoice.balance:.2f}"])
    if fiscal is not None and getattr(fiscal, "verification_code", ""):
        rows.append(["ZIMRA verification", fiscal.verification_code])

    table = Table([[Paragraph(_safe(key), styles["InvoiceCell"]),
                    Paragraph(_safe(value), styles["InvoiceCell"])]
                   for key, value in rows],
                  colWidths=[4.8 * cm, 12.8 * cm])
    table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f5f8f7")),
        ("GRID", (0, 0), (-1, -1), 0.3, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story += [table, Spacer(1, 0.5 * cm)]

    payments = list(invoice.payments.all().order_by("paid_on"))
    if payments:
        story.append(Paragraph("Payments received", styles["InvoiceH2"]))
        pay_rows = [["Receipt", "Date", "Method", "Amount"]]
        for payment in payments:
            pay_rows.append([
                payment.receipt_no or "-",
                timezone.localtime(payment.paid_on).strftime("%d %b %Y"),
                payment.get_method_display(),
                f"{currency}{payment.amount:.2f}",
            ])
        pay_table = Table(pay_rows, colWidths=[4.2 * cm, 4.0 * cm, 5.0 * cm, 4.4 * cm])
        pay_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef4f2")),
            ("GRID", (0, 0), (-1, -1), 0.3, RULE),
            ("ALIGN", (3, 1), (3, -1), "RIGHT"),
        ]))
        story += [pay_table, Spacer(1, 0.4 * cm)]

    story.append(Paragraph(
        "This invoice is due on the date shown. Please quote the invoice number "
        "when paying. Credit or debit notes are the only permitted corrections "
        "after issue.", styles["InvoiceSmall"]))

    doc.build(story)
    return buffer.getvalue()
