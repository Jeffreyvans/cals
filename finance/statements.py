"""PDF student statements."""

from io import BytesIO

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from core.branding import primary_colour
from core.document_header import build_header

from . import services


def build_statement_pdf(student):
    institution = student.institution
    currency = institution.currency
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=2 * cm,
                            rightMargin=2 * cm, topMargin=1.8 * cm,
                            bottomMargin=1.8 * cm)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="StatementTitle", parent=styles["Title"], fontName="Helvetica-Bold",
        fontSize=17, textColor=colors.HexColor(primary_colour(institution)),
        spaceAfter=4))
    story = [
        build_header(
            institution, "Student statement",
            subtitle=f"Generated {timezone.localdate():%d %B %Y}",
            title_style=styles["StatementTitle"],
            small_style=styles["BodyText"],
            logo_size=1.8 * cm,
            widths=(2.2 * cm, 10.0 * cm, 5.4 * cm),
            primary=primary_colour(institution)),
        Spacer(1, 0.35 * cm),
        Paragraph(f"{student.name} ({student.student_no})", styles["Normal"]),
        Spacer(1, 0.3 * cm),
    ]
    rows = [["Date", "Description", "Billed", "Paid", "Balance"]]
    for invoice in student.invoices.all().order_by("created"):
        rows.append([
            invoice.created.strftime("%d %b %Y"),
            invoice.description,
            f"{currency}{invoice.amount:.2f}",
            f"{currency}{invoice.paid:.2f}",
            f"{currency}{invoice.balance:.2f}",
        ])
    if len(rows) == 1:
        rows.append(["-", "No invoices", "", "", ""])
    table = Table(rows, colWidths=[2.7 * cm, 7.1 * cm, 2.3 * cm, 2.3 * cm, 2.6 * cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef4f2")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d9e2df")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(table)
    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph(
        f"Balance due: <b>{currency}{services.student_balance(student):.2f}</b>",
        styles["Heading2"]))
    credit = services.credit_balance(student)
    if credit:
        story.append(Paragraph(
            f"Credit on account: {currency}{credit:.2f}", styles["BodyText"]))
    doc.build(story)
    return buffer.getvalue()
