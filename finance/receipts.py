"""PDF payment receipts.

A receipt is the document a payer keeps, so it carries the institution's
branding: the logo, the primary colour and a QR code that opens the public
verification page. The QR code only proves the receipt is genuine; the page
never shows more than the receipt already does.
"""

import html
from io import BytesIO

import qrcode
from django.urls import reverse
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from core.branding import primary_colour
from core.document_header import build_header

INK = colors.HexColor("#0f172a")
MUTED = colors.HexColor("#64748b")
RULE = colors.HexColor("#e2e8f0")


def _safe(value):
    return html.escape(str(value if value is not None else ""))


def verify_url(payment, request=None):
    """The public verification link for this payment."""
    path = reverse("verify_receipt", args=[payment.verification_token])
    if request is not None:
        return request.build_absolute_uri(path)
    return path


def _qr(url, size=2.6 * cm):
    code = qrcode.QRCode(box_size=4, border=1)
    code.add_data(url)
    code.make(fit=True)
    buffer = BytesIO()
    code.make_image(fill_color="black", back_color="white").save(buffer, format="PNG")
    return Image(BytesIO(buffer.getvalue()), width=size, height=size)


def _styles(primary):
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="ReceiptTitle", parent=styles["Title"], fontName="Helvetica-Bold",
        fontSize=20, leading=24, textColor=colors.HexColor(primary), spaceAfter=2))
    styles.add(ParagraphStyle(
        name="ReceiptH2", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=12, leading=15, textColor=colors.HexColor(primary),
        spaceBefore=6, spaceAfter=4))
    styles.add(ParagraphStyle(
        name="ReceiptSmall", parent=styles["BodyText"], fontName="Helvetica",
        fontSize=8, leading=11, textColor=MUTED))
    return styles


def build_receipt_pdf(payment, request=None, fiscal=None):
    """PDF bytes for one payment receipt."""
    invoice = payment.invoice
    student = invoice.student
    institution = student.institution
    currency = payment.currency or institution.currency
    primary = primary_colour(institution)
    styles = _styles(primary)

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=2 * cm,
                            rightMargin=2 * cm, topMargin=1.8 * cm,
                            bottomMargin=1.8 * cm)

    layout = getattr(fiscal, "layout", None) if fiscal is not None else None
    story = [
        build_header(
            institution, "Payment receipt",
            subtitle=institution.tagline if not layout else "",
            title_style=styles["ReceiptTitle"],
            small_style=styles["ReceiptSmall"],
            header=layout, logo_size=1.8 * cm,
            widths=(2.2 * cm, 10.0 * cm, 5.4 * cm), primary=primary),
        Spacer(1, 0.4 * cm),
    ]

    method = payment.get_method_display()
    if payment.reference:
        method = f"{method} · {payment.reference}"
    rows = [
        ["Receipt number", payment.receipt_no or "-"],
        ["Student", f"{student.name} ({student.student_no or '-'})"],
        ["For", invoice.description],
        ["Amount received", f"{currency}{payment.amount:.2f}"],
        ["Method", method],
        ["Date", timezone.localtime(payment.paid_on).strftime("%d %B %Y, %H:%M")],
        ["Received by", payment.received_by_name],
        ["Balance on this invoice", f"{currency}{invoice.balance:.2f}"],
    ]
    if fiscal is not None and getattr(fiscal, "verification_code", ""):
        rows.append(["ZIMRA verification", fiscal.verification_code])
    table = Table(rows, colWidths=[4.6 * cm, 12.4 * cm])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor(primary)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, RULE),
    ]))
    story += [table, Spacer(1, 0.6 * cm)]

    url = verify_url(payment, request)
    qr = _qr(url)
    qr.hAlign = "LEFT"
    story += [qr, Spacer(1, 0.2 * cm),
              Paragraph(f"Verify this receipt: {_safe(url)}", styles["ReceiptSmall"]),
              Spacer(1, 0.4 * cm),
              Paragraph(
                  "This receipt confirms the payment recorded above. "
                  "Keep it for your records.", styles["ReceiptSmall"])]
    doc.build(story)
    return buffer.getvalue()
