"""Branded PDF report cards, transcripts and timetables with QR verification."""

import html
from decimal import Decimal
from io import BytesIO

import qrcode
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

from core.audit import log_action
from core.branding import accent_colour, primary_colour, readable_text
from core.logo import logo_flowable

from .models import DAY_CHOICES, DocumentVerification, GradeBand
from .services import enrollment_credits

PAGE_WIDTH, PAGE_HEIGHT = A4
INK = colors.HexColor("#0f172a")
MUTED = colors.HexColor("#64748b")
RULE = colors.HexColor("#e2e8f0")


def issue_document(student, kind, user, request=None):
    """Return the stable verification token for this student and document kind."""
    document, created = DocumentVerification.objects.get_or_create(
        student=student, kind=kind, defaults={"issued_by": user})
    if created:
        log_action(user, "academics.document_issued", document, request=request,
                   after={"student": student.pk, "kind": kind})
    return document


def _safe(value):
    return html.escape(str(value if value is not None else ""))


def _number(value):
    if value is None:
        return "—"
    if isinstance(value, Decimal):
        return f"{value:g}"
    return str(value)


def _qr_image(url):
    code = qrcode.QRCode(box_size=4, border=1)
    code.add_data(url)
    code.make(fit=True)
    buffer = BytesIO()
    code.make_image(fill_color="black", back_color="white").save(buffer, format="PNG")
    return Image(BytesIO(buffer.getvalue()), width=2.6 * cm, height=2.6 * cm)


def _styles(primary):
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="DocTitle", parent=styles["Title"], fontName="Helvetica-Bold",
        fontSize=18, leading=21, textColor=colors.HexColor(primary), spaceAfter=2))
    styles.add(ParagraphStyle(
        name="DocH2", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=11, leading=14, textColor=colors.HexColor(primary),
        spaceBefore=9, spaceAfter=3))
    styles.add(ParagraphStyle(
        name="DocBody", parent=styles["BodyText"], fontName="Helvetica",
        fontSize=9, leading=12, textColor=INK))
    styles.add(ParagraphStyle(
        name="DocSmall", parent=styles["BodyText"], fontName="Helvetica",
        fontSize=8, leading=10, textColor=MUTED))
    styles.add(ParagraphStyle(
        name="DocCell", parent=styles["BodyText"], fontName="Helvetica",
        fontSize=8, leading=10, textColor=INK))
    return styles


def _header(student, kind, styles, issued_on):
    institution = student.institution
    primary = primary_colour(institution)
    title = ("Academic transcript" if kind == DocumentVerification.Kind.TRANSCRIPT
             else "Report card")
    logo = logo_flowable(institution, 1.9 * cm)
    identity = [Paragraph(_safe(institution.name), styles["DocTitle"])]
    if institution.address:
        identity.append(Paragraph(_safe(institution.address), styles["DocSmall"]))
    if institution.contact_email:
        identity.append(Paragraph(_safe(institution.contact_email), styles["DocSmall"]))
    right = [Paragraph(title, styles["DocTitle"]),
             Paragraph(f"Issued {issued_on:%d %B %Y}", styles["DocSmall"])]
    cells = [[logo or "", identity, right]]
    table = Table(cells, colWidths=[2.2 * cm, 10.3 * cm, 5.5 * cm])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("LINEBELOW", (0, 0), (-1, -1), 1.2, colors.HexColor(primary)),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _student_table(student, styles):
    class_label = (student.class_label if student.school_class_id
                   else f"Year {student.year_or_grade}")
    rows = [
        ["Student", _safe(student.name), "Student number", _safe(student.student_no)],
        ["Programme", _safe(student.programme.name), "Class / year", _safe(class_label)],
    ]
    table = Table(rows, colWidths=[2.5 * cm, 6.7 * cm, 3.0 * cm, 5.8 * cm])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("TEXTCOLOR", (0, 0), (0, -1), MUTED),
        ("TEXTCOLOR", (2, 0), (2, -1), MUTED),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -2), 0.3, RULE),
    ]))
    return table


def _summary_table(context, styles):
    average = _number(context.get("average"))
    gpa = _number(context.get("gpa"))
    award = context.get("award") or "—"
    credits = context.get("credits") or 0
    sessions = context.get("attendance_sessions") or 0
    present = context.get("attendance_present") or 0
    pct = context.get("attendance_pct")
    attendance = f"{present} of {sessions}"
    if pct is not None:
        attendance += f" ({pct}%)"
    labels = [["Average mark", "GPA", "Projected award", "Credits", "Attendance"]]
    values = [[average, gpa, _safe(award), str(credits), attendance]]
    table = Table(labels + values,
                  colWidths=[3.1 * cm, 2.5 * cm, 5.0 * cm, 2.4 * cm, 5.0 * cm])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, 0), 7.5),
        ("FONTSIZE", (0, 1), (-1, 1), 9),
        ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
        ("TEXTCOLOR", (0, 1), (-1, 1), INK),
        ("GRID", (0, 0), (-1, -1), 0.3, RULE),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f5f8f7")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return table


def _term_table(term_data, bands, styles, primary):
    term, _term_obj, rows, sessions, present, pct, term_average, term_gpa, \
        position, cohort_size, term_hidden = term_data
    story = [Paragraph(_safe(term), styles["DocH2"])]
    facts = []
    if not term_hidden:
        facts.append(f"Term average: {_number(term_average)}")
        facts.append(f"GPA: {_number(term_gpa)}")
    if position:
        facts.append(f"Class position: {position} of {cohort_size}")
    facts.append(f"Attendance: {present} of {sessions}"
                 + (f" ({pct}%)" if pct is not None else ""))
    if term_hidden:
        facts.insert(0, "Results awaiting release or withheld")
    story.append(Paragraph(_safe(" · ".join(facts)), styles["DocSmall"]))

    data = [["Course or subject", "Credits", "Mark", "Grade", "Points", "Position"]]
    for enrollment in rows:
        shown = getattr(enrollment, "result_state", "shown") == "shown"
        if shown:
            mark = _number(enrollment.mark)
            grade, points = GradeBand.grade_for(enrollment.mark, bands)
            if enrollment.mark is None:
                grade, points = "—", "—"
            mark = _safe(mark)
        else:
            mark = _safe(enrollment.result_note)
            grade, points = "—", "—"
        row_position = getattr(enrollment, "position", None)
        position_text = (f"{row_position} of {enrollment.cohort_size}"
                         if shown and row_position else "—")
        data.append([
            Paragraph(_safe(enrollment.label), styles["DocCell"]),
            str(enrollment_credits(enrollment)),
            Paragraph(mark, styles["DocCell"]),
            grade,
            _number(points),
            position_text,
        ])
    if len(data) == 1:
        data.append(["No enrolments for this term.", "", "", "", "", ""])
    table = Table(data, colWidths=[6.6 * cm, 1.6 * cm, 3.4 * cm,
                                   1.6 * cm, 1.6 * cm, 3.2 * cm], repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("TEXTCOLOR", (0, 0), (-1, 0), readable_text(primary)),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(primary)),
        ("TEXTCOLOR", (0, 1), (-1, -1), INK),
        ("GRID", (0, 0), (-1, -1), 0.3, RULE),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(table)
    return story


def _footer(institution_name, title):
    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(doc.leftMargin, 1.0 * cm,
                          f"{institution_name} · {title}")
        canvas.drawRightString(PAGE_WIDTH - doc.rightMargin, 1.0 * cm,
                               f"Page {doc.page}")
        canvas.restoreState()
    return draw


def build_academic_document(student, context, kind, verify_url):
    """Return PDF bytes for a report card or transcript."""
    institution = student.institution
    primary = primary_colour(institution)
    accent = accent_colour(institution)
    styles = _styles(primary)
    title = ("Academic transcript" if kind == DocumentVerification.Kind.TRANSCRIPT
             else "Report card")
    issued_on = timezone.localdate()

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=1.5 * cm, rightMargin=1.5 * cm,
        topMargin=1.2 * cm, bottomMargin=1.8 * cm,
        title=f"{title} - {student.name}", author=institution.name)
    story = [
        _header(student, kind, styles, issued_on),
        Spacer(1, 0.35 * cm),
        _student_table(student, styles),
        Spacer(1, 0.35 * cm),
        _summary_table(context, styles),
        Spacer(1, 0.2 * cm),
    ]
    for term_data in context["terms"]:
        story.extend(_term_table(term_data, context.get("bands"), styles, primary))
    if not context["terms"]:
        story.append(Paragraph("No results have been recorded yet.", styles["DocBody"]))

    verify = Paragraph(
        "<b>Verify this document</b><br/>"
        f"Scan the code or visit {_safe(verify_url)}", styles["DocSmall"])
    qr_table = Table([[_qr_image(verify_url), verify]],
                     colWidths=[3.2 * cm, 14.8 * cm])
    qr_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#f5f8f7")),
        ("BOX", (0, 0), (-1, -1), 0.5, accent),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.extend([Spacer(1, 0.55 * cm), qr_table])
    document.build(story, onFirstPage=_footer(institution.name, title),
                   onLaterPages=_footer(institution.name, title))
    return buffer.getvalue()


def build_timetable_pdf(institution, title, entries, term=None, subtitle=""):
    """Return PDF bytes for a printable weekly timetable.

    The grid is one column per day that has lessons and one row per distinct
    time slot, so a school with uneven lesson lengths still gets a truthful
    timetable. Landscape A4 keeps room for the teacher and venue in each cell.
    """
    primary = primary_colour(institution)
    styles = _styles(primary)
    days = sorted({e.day for e in entries})
    slots = sorted({(e.start_time, e.end_time) for e in entries})
    by_cell = {}
    for e in entries:
        by_cell.setdefault((e.day, e.start_time, e.end_time), []).append(e)

    buffer = BytesIO()
    page = landscape(A4)
    document = SimpleDocTemplate(
        buffer, pagesize=page, leftMargin=1.1 * cm, rightMargin=1.1 * cm,
        topMargin=1.1 * cm, bottomMargin=1.6 * cm,
        title=f"{title}", author=institution.name)

    logo = logo_flowable(institution, 1.9 * cm)
    heading = [Paragraph(_safe(institution.name), styles["DocTitle"])]
    if institution.address:
        heading.append(Paragraph(_safe(institution.address), styles["DocSmall"]))
    right = [Paragraph(_safe(title), styles["DocTitle"]),
             Paragraph(_safe(subtitle or (str(term) if term else "")),
                       styles["DocSmall"]),
             Paragraph(f"Printed {timezone.localdate():%d %B %Y}", styles["DocSmall"])]
    head = Table([[logo or "", heading, right]],
                 colWidths=[2.6 * cm, 17.5 * cm, 7.4 * cm])
    head.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("LINEBELOW", (0, 0), (-1, -1), 1.2, colors.HexColor(primary)),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))

    story = [head, Spacer(1, 0.4 * cm)]
    if not days:
        story.append(Paragraph("No lessons are on this timetable yet.",
                               styles["DocBody"]))
        document.build(story)
        return buffer.getvalue()

    day_labels = {d: label for d, label in DAY_CHOICES}
    usable = page[0] - 2.2 * cm
    time_width = 3.0 * cm
    day_width = (usable - time_width) / len(days)
    data = [["Time"] + [day_labels.get(d, str(d)) for d in days]]
    for start, end in slots:
        row = [Paragraph(f"{start:%H:%M} –<br/>{end:%H:%M}", styles["DocSmall"])]
        for d in days:
            lessons = by_cell.get((d, start, end), [])
            if not lessons:
                row.append("")
                continue
            parts = []
            for lesson in lessons:
                teacher = (lesson.lecturer.get_full_name()
                           or lesson.lecturer.username)
                lines = [f"<b>{_safe(lesson.title)}</b>",
                         _safe(lesson.group)]
                if lesson.venue:
                    lines.append(_safe(lesson.venue))
                lines.append(_safe(teacher))
                parts.append("<br/>".join(lines))
            row.append(Paragraph("<br/>".join(parts), styles["DocCell"]))
        data.append(row)

    grid = Table(data, colWidths=[time_width] + [day_width] * len(days),
                repeatRows=1)
    grid.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("TEXTCOLOR", (0, 0), (-1, 0), readable_text(primary)),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(primary)),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("TEXTCOLOR", (0, 1), (-1, -1), INK),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f5f8f7")]),
    ]))
    story.append(grid)
    document.build(story, onFirstPage=_footer(institution.name, title),
                   onLaterPages=_footer(institution.name, title))
    return buffer.getvalue()
