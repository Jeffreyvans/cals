"""Institution logos.

A logo is validated, re-encoded and stored as a small set of WebP files with a
PNG fallback. Re-encoding with Pillow drops any metadata or hidden payload the
original file might carry, and random names stop one institution from guessing
another institution's paths.

Only PNG, JPG and WebP are accepted. SVG is refused on purpose: an uploaded SVG
can carry scripts, so it never reaches the browser or a PDF.
"""

import secrets
from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils.text import slugify
from PIL import Image, ImageOps, UnidentifiedImageError

# The largest file an administrator may upload.
MAX_BYTES = 2 * 1024 * 1024
# A logo smaller than this turns to mush in the sidebar and on a receipt.
MIN_SIDE = 200
# The widths that are actually rendered: sidebar, header and a print size.
SIZES = (64, 160, 512)
ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP"}


def logo_folder(institution):
    """The per-institution folder every logo file lives under."""
    slug = slugify(getattr(institution, "slug", "") or getattr(institution, "name", ""))
    return f"institutions/{slug or 'institution'}/logos"


def _read_bytes(uploaded):
    if hasattr(uploaded, "read"):
        return uploaded.read()
    return bytes(uploaded)


def open_logo(uploaded):
    """Validate an upload and return a clean RGBA image, or raise ValidationError.

    The real file type is read with Pillow, never trusted from the file name or
    the browser's content type, so a renamed script is still refused.
    """
    raw = _read_bytes(uploaded)
    if not raw:
        raise ValidationError("Choose a logo file first.")
    if len(raw) > MAX_BYTES:
        raise ValidationError("The logo must be 2 MB or smaller.")
    try:
        image = Image.open(BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError):
        raise ValidationError(
            "That file is not a picture. Use a PNG, JPG or WebP image."
        ) from None
    if (image.format or "").upper() not in ALLOWED_FORMATS:
        raise ValidationError(
            "Use a PNG, JPG or WebP image. SVG and other formats are not accepted."
        )
    if image.width < MIN_SIDE or image.height < MIN_SIDE:
        raise ValidationError(
            f"The logo must be at least {MIN_SIDE}×{MIN_SIDE} pixels."
        )
    # Re-encoding from a fresh RGBA image drops EXIF, comments and any payload.
    return ImageOps.exif_transpose(image).convert("RGBA")


def render_logo(image, size):
    """A copy of ``image`` shrunk to fit inside ``size`` (never upscaled)."""
    copy = image.copy()
    copy.thumbnail((size, size), Image.LANCZOS)
    return copy


def preview_png_bytes(uploaded, size=512):
    """A small PNG for a live preview or for the wizard's session copy."""
    image = open_logo(uploaded)
    buffer = BytesIO()
    render_logo(image, size).save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _store(name, image, image_format, **options):
    buffer = BytesIO()
    image.save(buffer, format=image_format, **options)
    return default_storage.save(name, ContentFile(buffer.getvalue()))


def _stored_paths(institution):
    """The size paths recorded in the institution's settings."""
    if not institution:
        return {}
    return dict((institution.settings or {}).get("logo") or {})


def _delete_files(names):
    for name in {name for name in names if name}:
        try:
            default_storage.delete(name)
        except Exception:  # pragma: no cover - a missing file must not stop a save
            pass


def _delete_stored(institution):
    names = list(_stored_paths(institution).values())
    logo = getattr(institution, "logo", None)
    if logo and logo.name:
        names.append(logo.name)
    _delete_files(names)


def save_logo(institution, uploaded):
    """Validate, re-encode and store ``uploaded`` for ``institution``.

    Returns the mapping of size to stored path. The 512 px WebP is also set as
    the canonical ``Institution.logo`` value.
    """
    image = open_logo(uploaded)
    folder = logo_folder(institution)
    token = secrets.token_hex(8)
    paths = {}
    for size in SIZES:
        name = f"{folder}/{token}_{size}.webp"
        paths[str(size)] = _store(name, render_logo(image, size), "WEBP",
                                  quality=88, method=6)
    png_name = f"{folder}/{token}_512.png"
    paths["png"] = _store(png_name, render_logo(image, 512), "PNG", optimize=True)

    _delete_stored(institution)
    institution.logo.name = paths["512"]
    settings_row = dict(institution.settings or {})
    settings_row["logo"] = paths
    institution.settings = settings_row
    institution.save(update_fields=["logo", "settings"])
    return paths


def remove_logo(institution):
    """Delete every stored logo file and clear the field and settings."""
    _delete_stored(institution)
    institution.logo = ""
    settings_row = dict(institution.settings or {})
    settings_row.pop("logo", None)
    institution.settings = settings_row
    institution.save(update_fields=["logo", "settings"])


def _name_for(institution, size):
    name = _stored_paths(institution).get(str(size))
    if name:
        return name
    logo = getattr(institution, "logo", None)
    return logo.name if logo and logo.name else ""


def _png_name(institution):
    name = _stored_paths(institution).get("png")
    if name:
        return name
    logo = getattr(institution, "logo", None)
    return logo.name if logo and logo.name else ""


def _url_for(name):
    if not name:
        return ""
    try:
        return default_storage.url(name)
    except Exception:  # pragma: no cover - a broken storage must not 500 a page
        return ""


def institution_logo_url(institution, size=160):
    """The URL of the stored logo at ``size``, or an empty string."""
    return _url_for(_name_for(institution, size))


def institution_logo_png_url(institution):
    """The URL of the PNG fallback, for clients that cannot read WebP."""
    return _url_for(_png_name(institution))


def institution_logo_bytes(institution, size=160, png=False):
    """The raw bytes of the stored logo, or None. Used to embed in PDFs."""
    name = _png_name(institution) if png else _name_for(institution, size)
    if not name:
        return None
    try:
        with default_storage.open(name, "rb") as handle:
            return handle.read()
    except (OSError, ValueError):
        return None


def absolute_logo_url(institution, request=None, size=160):
    """The logo URL made absolute, for emails and other off-site documents."""
    url = institution_logo_url(institution, size)
    if not url:
        return ""
    if request is not None:
        return request.build_absolute_uri(url)
    return url


def logo_flowable(institution, size):
    """A reportlab image flowable of the logo, or None.

    The PNG fallback is used because reportlab reads PNG and JPG directly.
    """
    data = institution_logo_bytes(institution, png=True)
    if not data:
        return None
    from reportlab.platypus import Image as FlowableImage

    try:
        image = FlowableImage(BytesIO(data))
    except Exception:  # pragma: no cover - a corrupt file must not stop a PDF
        return None
    scale = min(size / image.drawWidth, size / image.drawHeight, 1)
    image.drawWidth *= scale
    image.drawHeight *= scale
    image.hAlign = "LEFT"
    return image
