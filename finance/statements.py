"""PDF student statements."""

from io import BytesIO

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from core.branding import primary_colour
from core.logo import logo_flowable

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
    heading = [
        Paragraph(institution.name, styles["StatementTitle"]),
        Paragraph("Student statement", styles["Heading2"]),
        Paragraph(f"{student.name} ({student.student_no})", styles["Normal"]),
        Paragraph(f"Generated {timezone.localdate():%d %B %Y}",
                  styles["BodyText"]),
    ]
    logo = logo_flowable(institution, 1.6 * cm)
    if logo is not None:
        header = Table([[logo, heading]], colWidths=[2.2 * cm, 14.8 * cm])
        header.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
        story = [header, Spacer(1, 0.4 * cm)]
    else:
        story = heading + [Spacer(1, 0.4 * cm)]
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
