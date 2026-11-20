"""The one branded masthead every printable document uses.

A receipt, a statement, an invoice, a report card, a transcript, an identity
card and an email all show the same thing at the top: the institution's logo
(or its initials), its name, address, phone, email, website and, where the
school has them, the VAT and BP numbers. Keeping that in one place means a
detail can never be shown on a receipt but forgotten on a statement.

``build_header`` returns a reportlab flowable for PDFs; ``header_context``
returns the same facts for HTML templates and emails. A document that has been
fiscalised passes the snapshot stored on the submission instead of the live
institution, so a later logo or address change never rewrites a document that
was already issued.
"""

import html
import secrets
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils.text import slugify
from reportlab.graphics.shapes import Circle, Drawing, String
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import Image, Paragraph, Table, TableStyle

from config.brand import POWERED_BY
from core.branding import primary_colour, readable_text
from core.logo import institution_logo_bytes, institution_logo_url, logo_flowable

# The 512 px file is used for print; the 160 px file for screens and email.
PDF_LOGO = 512
SCREEN_LOGO = 160


def platform_attribution(institution):
    """Small platform credit for documents, or '' for a white-label tenant.

    Institution names, logos and colours are never changed by this credit. A
    platform admin can switch it off per institution for premium plans.
    """
    return POWERED_BY if getattr(institution, "show_platform_branding", True) else ""


def safe(value):
    """HTML-escape one value, keeping empty values empty rather than ``None``."""
    return html.escape(str(value if value is not None else ""))


def institution_details(institution):
    """The details printed in a document header.

    The VAT and BP numbers are included only when the institution has entered
    them, so a non-registered school does not show an empty tax line.
    """
    if institution is None:
        return {
            "name": "", "address": "", "phone": "", "email": "", "website": "",
            "vat_number": "", "bp_number": "", "initials": "?",
        }
    return {
        "name": getattr(institution, "name", "") or "",
        "address": getattr(institution, "address", "") or "",
        "phone": getattr(institution, "contact_phone", "") or "",
        "email": getattr(institution, "contact_email", "") or "",
        "website": getattr(institution, "website", "") or "",
        "vat_number": getattr(institution, "vat_number", "") or "",
        "bp_number": getattr(institution, "bp_number", "") or "",
        "initials": institution.initials() if hasattr(institution, "initials") else "?",
    }


def contact_lines(details):
    """The small print under the name: address, then contact details."""
    lines = []
    if details.get("address"):
        lines.append(details["address"])
    contacts = " · ".join(filter(None, [
        details.get("phone"), details.get("email"), details.get("website"),
    ]))
    if contacts:
        lines.append(contacts)
    tax = " · ".join(filter(None, [
        f"VAT {details['vat_number']}" if details.get("vat_number") else "",
        f"BP {details['bp_number']}" if details.get("bp_number") else "",
    ]))
    if tax:
        lines.append(tax)
    return lines


def _hex(value, fallback="#1E3A8A"):
    try:
        return colors.HexColor(value or fallback)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return colors.HexColor(fallback)


def initials_flowable(initials, size, background=None, text_colour=None):
    """A soft-coloured circle with the institution's initials in it."""
    background = background or "#1E3A8A"
    text_colour = text_colour or readable_text(background)
    drawing = Drawing(size, size)
    drawing.add(Circle(
        size / 2, size / 2, size / 2,
        fillColor=_hex(background), strokeColor=None))
    drawing.add(String(
        size / 2, size / 2 - size * 0.13, (initials or "?")[:2],
        fontName="Helvetica-Bold", fontSize=size * 0.36,
        fillColor=_hex(text_colour), textAnchor="middle"))
    return drawing


def _flowable_from_name(name, size):
    """A reportlab image for a stored file, or None when it cannot be read."""
    if not name:
        return None
    try:
        with default_storage.open(name, "rb") as handle:
            data = handle.read()
        image = Image(BytesIO(data))
    except Exception:  # a missing or corrupt file must not break a PDF
        return None
    # Scale to the requested box while keeping the aspect ratio.
    try:
        scale = min(size / image.drawWidth, size / image.drawHeight, 1)
    except (TypeError, ZeroDivisionError):  # pragma: no cover - defensive
        return None
    image.drawWidth *= scale
    image.drawHeight *= scale
    image.hAlign = "LEFT"
    return image


def header_logo(institution, header=None, size=1.9 * cm):
    """The logo flowable for a live institution or a stored layout snapshot."""
    if header and header.get("logo"):
        image = _flowable_from_name(header["logo"], size)
        if image is not None:
            return image
    return logo_flowable(institution, size)


def header_mark(institution, header=None, size=1.9 * cm, primary=None):
    """The logo, or an initials circle when there is no usable logo."""
    logo = header_logo(institution, header=header, size=size)
    if logo is not None:
        return logo
    details = header.get("details", {}) if header else institution_details(institution)
    primary = primary or primary_colour(institution)
    return initials_flowable(
        details.get("initials") or "?", size, primary, readable_text(primary))


def build_header(
    institution,
    title="",
    subtitle="",
    title_style=None,
    small_style=None,
    header=None,
    logo_size=1.9 * cm,
    widths=(2.2 * cm, 10.3 * cm, 5.5 * cm),
    primary=None,
    rule=True,
):
    """A three-column masthead: mark, institution details, document title.

    ``header`` is the snapshot stored on a fiscalised document. When it is
    given the live institution is ignored for the header, which is how an
    already-issued document keeps the layout it had when it was issued.
    """
    details = header.get("details", {}) if header else institution_details(institution)
    primary = primary or primary_colour(institution)
    mark = header_mark(institution, header=header, size=logo_size, primary=primary)
    identity = [Paragraph(safe(details.get("name")), title_style)]
    for line in contact_lines(details):
        identity.append(Paragraph(safe(line), small_style))

    right = []
    if title:
        right.append(Paragraph(safe(title), title_style))
    if subtitle:
        right.append(Paragraph(safe(subtitle), small_style))

    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    if rule:
        style.append(("LINEBELOW", (0, 0), (-1, -1), 1.2, _hex(primary)))
    table = Table([[mark, identity, right]], colWidths=list(widths))
    table.setStyle(TableStyle(style))
    return table


def _storage_url(name):
    if not name:
        return ""
    try:
        return default_storage.url(name)
    except Exception:  # pragma: no cover - a broken storage must not 500 a page
        return ""


def header_context(institution, layout=None, request=None, size=SCREEN_LOGO):
    """The template context for the shared HTML masthead."""
    if layout:
        details = layout.get("details", {})
        logo_url = _storage_url(layout.get("logo"))
    else:
        details = institution_details(institution)
        logo_url = institution_logo_url(institution, size)
    if request is not None and logo_url:
        logo_url = request.build_absolute_uri(logo_url)
    return {
        "details": details,
        "lines": contact_lines(details),
        "logo_url": logo_url,
        "initials": details.get("initials") or "?",
    }


def snapshot_header(institution):
    """Freeze the current header for a document about to be fiscalised.

    The logo is copied rather than referenced, because replacing the
    institution's logo deletes the old file. Without the copy a reprint of an
    old fiscal receipt would lose its mark.
    """
    details = institution_details(institution)
    logo_name = ""
    data = institution_logo_bytes(institution, png=True)
    if data:
        folder = slugify(getattr(institution, "slug", "") or details.get("name") or "institution")
        name = f"fiscalisation/headers/{folder}/{secrets.token_hex(12)}.png"
        try:
            logo_name = default_storage.save(name, ContentFile(data))
        except Exception:  # pragma: no cover - a storage failure keeps the details
            logo_name = ""
    return {"details": details, "logo": logo_name}
