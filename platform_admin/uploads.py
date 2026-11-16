"""Safe handling of the two optional application documents.

An upload is never stored as it arrived. A picture is decoded and re-encoded so
metadata and hidden payloads cannot survive, and a PDF is checked for the
dangerous features that turn a document into a script. Only PDF, PNG, JPEG and
WEBP are accepted; SVG and HTML are refused.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, UnidentifiedImageError

MAX_BYTES = 5 * 1024 * 1024
ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "webp"}
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
PDF_TOKENS = (b"/JavaScript", b"/JS", b"/Launch", b"/EmbeddedFile", b"/OpenAction")


@dataclass
class SafeUpload:
    content: ContentFile
    name: str
    content_type: str
    size: int
    sha256: str


def _extension(upload):
    name = getattr(upload, "name", "") or ""
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def sanitise_upload(upload, stem="document"):
    """Validate and re-encode one uploaded file. Raises ValidationError."""
    if upload is None:
        return None
    size = getattr(upload, "size", 0)
    if size <= 0:
        raise ValidationError("The uploaded file is empty.")
    if size > MAX_BYTES:
        raise ValidationError("Each document must be 5 MB or smaller.")
    extension = _extension(upload)
    if extension not in ALLOWED_EXTENSIONS:
        raise ValidationError("Upload a PDF, PNG, JPG or WEBP file.")

    data = upload.read()
    if not data:
        raise ValidationError("The uploaded file is empty.")
    digest = hashlib.sha256(data).hexdigest()

    if extension == "pdf":
        if not data.lstrip().startswith(b"%PDF-"):
            raise ValidationError("That file is not a valid PDF.")
        for token in PDF_TOKENS:
            if token in data:
                raise ValidationError(
                    "That PDF contains active content and cannot be accepted."
                )
        return SafeUpload(
            content=ContentFile(data),
            name=f"{stem}.pdf",
            content_type="application/pdf",
            size=len(data),
            sha256=digest,
        )

    try:
        image = Image.open(BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValidationError("That image could not be read.") from exc
    if image.format not in {"PNG", "JPEG", "WEBP"}:
        raise ValidationError("Upload a PNG, JPG or WEBP image.")
    if image.width < 300 or image.height < 300:
        raise ValidationError("The image is too small to read. Use one at least 300 x 300.")
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    cleaned = output.getvalue()
    return SafeUpload(
        content=ContentFile(cleaned),
        name=f"{stem}.png",
        content_type="image/png",
        size=len(cleaned),
        sha256=hashlib.sha256(cleaned).hexdigest(),
    )
