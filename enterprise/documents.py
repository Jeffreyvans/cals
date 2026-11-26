"""Sharing-safe PDF documents for the sponsor portal.

Existing finance receipts are the school's record and include the student.
A sponsor at level 1 or 2 receives this redacted version instead: it proves the
payment and its amount, but never carries a student name, number or invoice
description.
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
from core.document_header import build_header, platform_attribution

INK = colors.HexColor("#0f172a")
RULE = colors.HexColor("#e2e8f0")


def build_sponsor_receipt_pdf(account, payment, request=None, student_name=None):
    """PDF bytes for one sponsor receipt.

    ``student_name`` is passed only when the sponsor reaches level 3 and holds
    a live institution grant for that student. Otherwise the receipt is fully
    redacted and proves only the payment, amount and sponsor.
    """
    institution = account.sponsor.institution
    primary = primary_colour(institution)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="SponsorReceiptTitle", parent=styles["Title"], fontSize=18, leading=22,
        textColor=colors.HexColor(primary), spaceAfter=4,
    ))
    small = ParagraphStyle(
        name="SponsorReceiptSmall", parent=styles["BodyText"], fontSize=9, leading=12,
        textColor=INK,
    )
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=1.8 * cm, bottomMargin=1.8 * cm,
        title=f"Sponsor receipt {payment.receipt_no or payment.pk}",
    )
    story = [
        build_header(
            institution, "Sponsor payment receipt", title_style=styles["SponsorReceiptTitle"],
            small_style=small, logo_size=1.8 * cm,
            widths=(2.2 * cm, 10.0 * cm, 5.4 * cm), primary=primary,
        ),
        Spacer(1, 0.4 * cm),
    ]
    rows = [
        ["Receipt number", payment.receipt_no or str(payment.pk)],
        ["Paid by", account.sponsor.name],
        ["Amount", f"{payment.currency}{payment.amount:.2f}"],
        ["Method", payment.get_method_display()],
        ["Date", timezone.localtime(payment.paid_on).strftime("%d %B %Y")],
        ["Institution", institution.name],
    ]
    if student_name:
        rows.append(["Student", student_name])
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
    note = (
        "This receipt confirms a sponsor payment and named student detail was "
        "released under a live consent grant."
        if student_name else
        "This redacted receipt confirms a sponsor payment. Student details are "
        "not shown at this sharing level."
    )
    story += [table, Spacer(1, 0.5 * cm), Paragraph(note, small)]
    credit = platform_attribution(institution)
    if credit:
        story += [Spacer(1, 0.25 * cm), Paragraph(html.escape(credit), small)]
    doc.build(story)
    return buffer.getvalue()
