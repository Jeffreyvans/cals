"""Printable payslips."""

import html
from io import BytesIO

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from core.branding import primary_colour
from core.document_header import platform_attribution


def _safe(value):
    return html.escape(str(value if value is not None else ""))


def _money(value):
    return f"{value:,.2f}" if value is not None else "0.00"


def build_payslip_pdf(payslip):
    """PDF bytes for one payslip."""
    run = payslip.run
    staff = payslip.staff
    institution = payslip.institution
    user = staff.user
    name = user.get_full_name() or user.username
    currency = institution.currency or ""
    primary = primary_colour(institution)
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="PayslipTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=17,
            textColor=colors.HexColor(primary),
            spaceAfter=4,
        )
    )
    styles.add(
        ParagraphStyle(
            name="PayslipSmall",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#5f6f6c"),
        )
    )
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=1.8 * cm,
        bottomMargin=1.8 * cm,
        title=f"Payslip {run.reference} - {name}",
        author=institution.name,
    )
    story = [
        Paragraph(_safe(institution.name), styles["PayslipTitle"]),
        Paragraph("Payslip", styles["Heading2"]),
        Paragraph(
            f"{_safe(name)} ({_safe(staff.employee_no or staff.pk)})",
            styles["BodyText"],
        ),
        Paragraph(
            f"{_safe(run.name)} &middot; {run.start_date:%d %b %Y} to "
            f"{run.end_date:%d %b %Y} &middot; paid {run.pay_date:%d %b %Y}",
            styles["PayslipSmall"],
        ),
        Paragraph(
            f"Reference {_safe(run.reference)} &middot; generated {timezone.localdate():%d %B %Y}",
            styles["PayslipSmall"],
        ),
        Spacer(1, 0.5 * cm),
    ]
    rows = [["Earnings", "Amount", "Deductions", "Amount"]]
    rows.append(
        [
            "Basic pay",
            f"{currency}{_money(payslip.basic)}",
            "Tax",
            f"{currency}{_money(payslip.tax)}",
        ]
    )
    rows.append(
        [
            "Allowances",
            f"{currency}{_money(payslip.allowances)}",
            "Pension",
            f"{currency}{_money(payslip.pension)}",
        ]
    )
    rows.append(["", "", "Other deduction", f"{currency}{_money(payslip.other_deduction)}"])
    table = Table(rows, colWidths=[5.0 * cm, 3.3 * cm, 5.0 * cm, 3.3 * cm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef4f2")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (1, 1), (1, -1), "RIGHT"),
                ("ALIGN", (3, 1), (3, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d9e2df")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 0.5 * cm))
    story.append(
        Paragraph(
            f"Gross pay: {currency}{_money(payslip.gross)} &nbsp;&nbsp; "
            f"Deductions: {currency}{_money(payslip.deductions)}",
            styles["BodyText"],
        )
    )
    story.append(
        Paragraph(
            f"Net pay: <b>{currency}{_money(payslip.net)}</b>",
            styles["Heading2"],
        )
    )
    credit = platform_attribution(institution)
    if credit:
        story += [Spacer(1, 0.25 * cm), Paragraph(credit, styles["PayslipSmall"])]
    doc.build(story)
    return buffer.getvalue()
