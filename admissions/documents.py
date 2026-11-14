"""Printable letters, certificates and identity cards.

Every public document carries a QR code that points at the verification page.
The page shows only enough to confirm the document is genuine, never marks or
money.
"""

import html
from io import BytesIO

import qrcode
from django.core.files.storage import default_storage
from django.urls import reverse
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
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

from core.branding import primary_colour, readable_text
from core.document_header import (
    build_header,
    contact_lines,
    header_mark,
    institution_details,
)

INK = colors.HexColor("#0f172a")
MUTED = colors.HexColor("#64748b")
RULE = colors.HexColor("#e2e8f0")


def _safe(value):
    return html.escape(str(value if value is not None else ""))


def _absolute(request, path):
    return request.build_absolute_uri(path) if request is not None else path


def _styles(primary):
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="AdTitle", parent=styles["Title"], fontName="Helvetica-Bold",
        fontSize=22, leading=26, textColor=colors.HexColor(primary), spaceAfter=4))
    styles.add(ParagraphStyle(
        name="AdH2", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=12, leading=15, textColor=colors.HexColor(primary),
        spaceBefore=10, spaceAfter=4))
    styles.add(ParagraphStyle(
        name="AdBody", parent=styles["BodyText"], fontName="Helvetica",
        fontSize=10, leading=14, textColor=INK))
    styles.add(ParagraphStyle(
        name="AdSmall", parent=styles["BodyText"], fontName="Helvetica",
        fontSize=8, leading=11, textColor=MUTED))
    styles.add(ParagraphStyle(
        name="AdCell", parent=styles["BodyText"], fontName="Helvetica",
        fontSize=9, leading=12, textColor=INK))
    return styles


def _photo(file_field, width, height):
    if not file_field:
        return ""
    try:
        with default_storage.open(file_field.name, "rb") as handle:
            image = Image(BytesIO(handle.read()), width=width, height=height)
    except (OSError, ValueError):
        return ""
    return image


def _qr(url, size=2.4 * cm):
    code = qrcode.QRCode(box_size=4, border=1)
    code.add_data(url)
    code.make(fit=True)
    buffer = BytesIO()
    code.make_image(fill_color="black", back_color="white").save(buffer, format="PNG")
    return Image(BytesIO(buffer.getvalue()), width=size, height=size)


def _header(institution, title, styles, subtitle=""):
    return build_header(
        institution, title, subtitle=subtitle,
        title_style=styles["AdTitle"], small_style=styles["AdSmall"],
        logo_size=1.8 * cm, widths=(2.2 * cm, 9.3 * cm, 6.0 * cm),
        primary=primary_colour(institution))


def _footer(text):
    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(1.8 * cm, 1.1 * cm, text)
        canvas.drawRightString(
            doc.pagesize[0] - 1.8 * cm, 1.1 * cm,
            f"Generated {timezone.localtime():%d %b %Y %H:%M}")
        canvas.restoreState()
    return draw


def build_offer_letter(offer, request=None):
    """PDF bytes for an offer letter."""
    application = offer.application
    institution = application.institution
    primary = primary_colour(institution)
    styles = _styles(primary)
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=1.8 * cm, rightMargin=1.8 * cm,
        topMargin=1.5 * cm, bottomMargin=1.8 * cm,
        title=f"Offer {offer.offer_no}", author=institution.name)
    verify_url = _absolute(request, reverse(
        "admissions:track_application", args=[application.tracking_token]))
    story = [
        _header(institution, "Offer of a place", styles,
                f"{offer.offer_no} · {offer.issued_at:%d %B %Y}"),
        Spacer(1, 0.6 * cm),
        Paragraph(f"Dear {_safe(application.name)}", styles["AdBody"]),
        Spacer(1, 0.3 * cm),
        Paragraph(
            f"We are pleased to offer you a place on <b>{_safe(application.programme)}</b>"
            f"{' starting in ' + _safe(application.year_or_grade) if application.year_or_grade else ''}. "
            "This offer is made on the strength of the information and results you supplied.",
            styles["AdBody"]),
        Spacer(1, 0.4 * cm),
    ]
    rows = [
        ["Offer number", offer.offer_no],
        ["Programme", str(application.programme or application.school_class or "—")],
        ["Acceptance deadline", offer.acceptance_deadline.strftime("%d %B %Y")],
        ["Deposit", f"{institution.currency}{offer.deposit_amount:,.2f}"],
        ["Status", offer.get_status_display()],
    ]
    table = Table([[Paragraph(_safe(k), styles["AdCell"]),
                    Paragraph(_safe(v), styles["AdCell"])] for k, v in rows],
                  colWidths=[5.2 * cm, 10.4 * cm])
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f4f7f6")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story += [table, Spacer(1, 0.5 * cm)]
    if offer.deposit_amount > 0:
        story.append(Paragraph(
            "To accept, confirm your details and record how the deposit is being paid. "
            "The place is confirmed only when the acceptance is submitted.",
            styles["AdBody"]))
        story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(
        f"Track and accept this offer online: {_safe(verify_url)}", styles["AdBody"]))
    qr = _qr(verify_url)
    qr.hAlign = "LEFT"
    story += [Spacer(1, 0.3 * cm), qr, Spacer(1, 0.3 * cm),
              Paragraph("The QR code opens the same page. Do not share this link.",
                        styles["AdSmall"])]
    doc.build(story, onFirstPage=_footer(institution.name),
              onLaterPages=_footer(institution.name))
    return buffer.getvalue()


def build_certificate(certificate, request=None):
    """PDF bytes for an award certificate."""
    student = certificate.student
    institution = student.institution
    primary = primary_colour(institution)
    styles = _styles(primary)
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4), leftMargin=2.0 * cm, rightMargin=2.0 * cm,
        topMargin=1.6 * cm, bottomMargin=1.6 * cm,
        title=f"Certificate {certificate.serial_no}", author=institution.name)
    verify_url = _absolute(request, reverse(
        "admissions:verify_certificate", args=[certificate.token]))
    story = [
        _header(institution, certificate.title, styles,
                f"Serial {certificate.serial_no}"),
        Spacer(1, 1.0 * cm),
        Paragraph("This is to certify that", styles["AdBody"]),
        Spacer(1, 0.4 * cm),
        Paragraph(_safe(student.name), styles["AdTitle"]),
        Paragraph(_safe(student.student_no), styles["AdSmall"]),
        Spacer(1, 0.7 * cm),
        Paragraph(
            f"has been awarded <b>{_safe(certificate.award)}</b> for the "
            f"programme <b>{_safe(student.programme.name)}</b>.",
            styles["AdBody"]),
        Spacer(1, 0.6 * cm),
        Paragraph(
            f"Graduation date: {certificate.graduation_date:%d %B %Y}", styles["AdBody"]),
        Spacer(1, 1.0 * cm),
    ]
    signature = Table([
        [Paragraph("Registrar / Principal", styles["AdSmall"]),
         Paragraph("Verification", styles["AdSmall"])],
        [Paragraph("________________________", styles["AdBody"]),
         _qr(verify_url, 2.0 * cm)],
        ["", Paragraph(_safe(verify_url), styles["AdSmall"])],
    ], colWidths=[15.0 * cm, 7.0 * cm])
    signature.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(signature)
    doc.build(story, onFirstPage=_footer(institution.name),
              onLaterPages=_footer(institution.name))
    return buffer.getvalue()


def _card_block(card, request, styles, primary):
    student = card.student
    institution = student.institution
    verify_url = _absolute(request, reverse(
        "admissions:verify_card", args=[card.token]))
    photo = _photo(card.photo or student.photo, 2.3 * cm, 3.0 * cm)
    front_details = [
        Paragraph(_safe(institution.name), styles["AdCell"]),
        Paragraph(f"<b>{_safe(student.name)}</b>", styles["AdBody"]),
        Paragraph(f"Student no: {_safe(student.student_no)}", styles["AdSmall"]),
        Paragraph(f"Programme: {_safe(student.programme.name)}", styles["AdSmall"]),
        Paragraph(f"Valid until: {card.valid_until:%d %b %Y}", styles["AdSmall"]),
    ]
    front = Table([[photo, front_details]], colWidths=[2.6 * cm, 5.6 * cm])
    front.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    card_logo = header_mark(institution, size=0.9 * cm, primary=primary)
    front_left = [card_logo]
    front_left.append(Paragraph(
        f"<b>{_safe(institution.name)}</b>", styles["AdBody"]))
    front_holder = Table([[front_left, _qr(verify_url, 1.5 * cm)]],
                         colWidths=[6.0 * cm, 2.2 * cm])
    front_holder.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(primary)),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor(readable_text(primary))),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    # The back repeats the address and tax numbers so a found card can be
    # returned and a cashier can see the institution's VAT/BP identity.
    details = institution_details(institution)
    back_lines = contact_lines(details)[:3]
    back_details = [
        Paragraph(f"Card {_safe(card.card_no)}", styles["AdCell"]),
        *[Paragraph(_safe(line), styles["AdSmall"]) for line in back_lines],
        Paragraph("If found, return to the institution.", styles["AdSmall"]),
    ]
    card_table = Table([
        [front_holder],
        [front],
        [Paragraph("This card remains the property of the institution.",
                   styles["AdSmall"])],
    ], colWidths=[8.4 * cm])
    card_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor(primary)),
        ("BACKGROUND", (0, 1), (0, 1), colors.white),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    back = Table([[Paragraph("<b>Back</b>", styles["AdCell"]),
                   back_details, _qr(verify_url, 1.6 * cm)]],
                 colWidths=[1.6 * cm, 4.8 * cm, 1.8 * cm])
    back.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return [card_table, Spacer(1, 0.3 * cm), back]


def build_id_card(card, request=None):
    """PDF bytes with the front and back of one student card."""
    return build_id_cards([card], request=request)


def build_id_cards(cards, request=None):
    """PDF bytes for one or more cards, two to a page."""
    cards = list(cards)
    institution = cards[0].student.institution if cards else None
    primary = primary_colour(institution)
    styles = _styles(primary)
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=1.8 * cm, rightMargin=1.8 * cm,
        topMargin=1.6 * cm, bottomMargin=1.6 * cm,
        title="Student identity cards",
        author=institution.name if institution else "EduSphere")
    story = []
    for index, card in enumerate(cards):
        story += _card_block(card, request, styles, primary)
        if index % 2 == 1:
            story.append(Spacer(1, 0.6 * cm))
        else:
            story.append(Spacer(1, 0.3 * cm))
    if not story:
        story = [Paragraph("No cards to print.", styles["AdBody"])]
    doc.build(story, onFirstPage=_footer(institution.name if institution else ""),
              onLaterPages=_footer(institution.name if institution else ""))
    return buffer.getvalue()
