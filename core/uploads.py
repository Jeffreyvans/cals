"""Shared, safe handling for every uploaded file in EduSphere.

Two rules apply to every upload:

* the browser's file name and content type are never trusted -- the real
  content is checked with Pillow (images) or the file signature (documents);
* a picture is re-encoded before it is stored, which drops EXIF/metadata and
  keeps the stored file small on a slow mobile connection.

The same helpers are used by model field validators, forms, the logo pipeline
and the platform application sanitiser, so every upload path gives the same
clear error message for a rejected type, size or dimension.
"""

from __future__ import annotations

import os
import re
from io import BytesIO

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

# --- limits ---------------------------------------------------------------
IMAGE_MAX_BYTES = 5 * 1024 * 1024          # 5 MB
DOCUMENT_MAX_BYTES = 10 * 1024 * 1024      # 10 MB
IMAGE_MIN_SIDE = 64                        # smaller than this is unreadable
IMAGE_MAX_DIMENSION = 1600                 # large photos are shrunk to this
IMAGE_QUALITY = 82
# Pillow refuses images above this many pixels before allocating a huge buffer.
Image.MAX_IMAGE_PIXELS = 24_000_000

IMAGE_FORMATS = {"PNG", "JPEG", "WEBP"}
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
DOCUMENT_EXTENSIONS = {
    "pdf", "png", "jpg", "jpeg", "webp",
    "doc", "docx", "xls", "xlsx", "ppt", "pptx",
    "odt", "ods", "csv", "txt", "zip",
}
# Anything script-like is refused even if it arrives with a benign extension.
DANGEROUS_EXTENSIONS = {
    "exe", "dll", "bat", "cmd", "com", "msi", "scr", "ps1", "sh", "bash",
    "js", "mjs", "php", "py", "rb", "pl", "html", "htm", "svg", "xml",
}

_EXTENSION_RE = re.compile(r"[^a-z0-9]+")


def _extension(name):
    base = os.path.basename(str(name or "")).lower()
    return base.rsplit(".", 1)[-1] if "." in base else ""


def _as_stream(upload):
    """Return a file-like object for an upload, bytes or a path.

    Most callers pass a Django ``UploadedFile``/``FieldFile``, but the setup
    wizard keeps a logo in the session as base64 and hands the decoded bytes
    back here. Wrapping those bytes keeps one validation path for every caller.
    """
    if hasattr(upload, "read"):
        return upload
    if isinstance(upload, (bytes, bytearray, memoryview)):
        return BytesIO(bytes(upload))
    return upload


def _size(upload):
    size = getattr(upload, "size", None)
    if size is not None:
        return int(size)
    if isinstance(upload, (bytes, bytearray, memoryview)):
        return len(upload)
    try:
        position = upload.tell()
    except (AttributeError, OSError):
        position = None
    try:
        upload.seek(0, os.SEEK_END)
        return int(upload.tell())
    except (AttributeError, OSError, ValueError):
        return 0
    finally:
        try:
            upload.seek(position if position is not None else 0)
        except (AttributeError, OSError):
            pass


def _rewind(upload):
    try:
        upload.seek(0)
    except (AttributeError, OSError):
        pass


def _human_size(limit):
    if limit % (1024 * 1024) == 0:
        return f"{limit // (1024 * 1024)} MB"
    return f"{limit // 1024} KB"


def _readable_size(size):
    """Show the actual size, for example 4.2 MB, in a rejected upload."""
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / 1024:.0f} KB"


def safe_stem(name, fallback="file"):
    """A short, filesystem-safe stem; never used as the full stored name."""
    stem = os.path.splitext(os.path.basename(str(name or "")))[0].lower()
    stem = _EXTENSION_RE.sub("-", stem).strip("-")
    return (stem or fallback)[:60]


# --- images ---------------------------------------------------------------

def open_image(upload, *, max_bytes=IMAGE_MAX_BYTES, min_side=IMAGE_MIN_SIDE,
               allowed_formats=None, label="image"):
    """Validate an image upload and return a clean, exif-corrected copy.

    Raises ``ValidationError`` with a message a person can act on. The file is
    inspected by Pillow, not by its name or the browser's content type.
    """
    allowed = {fmt.upper() for fmt in (allowed_formats or IMAGE_FORMATS)}
    upload = _as_stream(upload)
    size = _size(upload)
    if size <= 0:
        raise ValidationError(f"Choose a {label} file first (FILE-REJECTED).")
    if size > max_bytes:
        raise ValidationError(
            f"Input problem: The {label} is {_readable_size(size)}. "
            f"The limit is {_human_size(max_bytes)} (FILE-REJECTED)."
        )
    _rewind(upload)
    try:
        image = Image.open(upload)
        image_format = (image.format or "").upper()
        width, height = image.size
        # Check dimensions before ``load()`` so a decompression bomb never
        # allocates a huge buffer on the server.
        if width <= 0 or height <= 0:
            raise ValueError("empty image")
        if image_format not in allowed:
            raise ValidationError(
                f"Use a {', '.join(sorted(allowed))} image. "
                "SVG and other formats are not accepted (FILE-REJECTED)."
            )
        if min_side and (width < min_side or height < min_side):
            raise ValidationError(
                f"The {label} must be at least {min_side}x{min_side} pixels."
            )
        image.load()
    except ValidationError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise ValidationError(
            f"That file is not a picture. Use a {', '.join(sorted(allowed))} image "
            "(FILE-REJECTED)."
        ) from None
    return ImageOps.exif_transpose(image).convert("RGBA" if image_format == "PNG" else "RGB")


def resize_image(image, *, max_dimension=IMAGE_MAX_DIMENSION):
    """Shrink ``image`` to fit inside ``max_dimension``; never upscale."""
    copy = image.copy()
    if max(copy.size) > max_dimension:
        copy.thumbnail((max_dimension, max_dimension), Image.LANCZOS)
    return copy


def image_bytes(image, *, image_format="WEBP", quality=IMAGE_QUALITY, max_dimension=None):
    """Encode an image to bytes, optionally shrinking it first."""
    if max_dimension:
        image = resize_image(image, max_dimension=max_dimension)
    output = BytesIO()
    options = {"quality": quality, "method": 6} if image_format == "WEBP" else {}
    if image_format == "JPEG":
        image = image.convert("RGB")
        options = {"quality": quality, "optimize": True}
    if image_format == "PNG":
        options = {"optimize": True}
    image.save(output, format=image_format, **options)
    return output.getvalue()


def processed_image(upload, *, max_dimension=IMAGE_MAX_DIMENSION, quality=IMAGE_QUALITY,
                    max_bytes=IMAGE_MAX_BYTES, min_side=IMAGE_MIN_SIDE,
                    label="image", stem=None, image_format="WEBP"):
    """Validate, shrink and re-encode an uploaded picture.

    Returns a ``ContentFile`` ready to assign to a model field, or raises
    ``ValidationError``. A large photo is never kept at its original size.
    """
    image = open_image(
        upload, max_bytes=max_bytes, min_side=min_side, label=label)
    data = image_bytes(
        image, image_format=image_format, quality=quality,
        max_dimension=max_dimension)
    name = f"{safe_stem(stem or getattr(upload, 'name', 'photo'), 'photo')}." \
           f"{image_format.lower()}"
    return ContentFile(data, name=name)


def image_upload_validator(upload):
    """Model/form validator for a picture field."""
    if not upload:
        return
    open_image(upload, label="photo")


def shrink_image_field(instance, field_name, *, max_dimension=IMAGE_MAX_DIMENSION,
                       quality=IMAGE_QUALITY):
    """Re-encode a newly assigned ImageField in place, before the row is saved.

    Only a new (uncommitted) upload is touched, so saving an existing row again
    never re-encodes the stored file. Returns True when a change was made.
    """
    field = getattr(instance, field_name, None)
    if not field or not getattr(field, "file", None):
        return False
    handle = field.file
    if getattr(handle, "_committed", True):
        return False
    try:
        image = open_image(handle, label="photo")
        width, height = image.size
        needs_resize = max(width, height) > max_dimension
        needs_reencode = getattr(field, "size", 0) > 500 * 1024
        if not (needs_resize or needs_reencode):
            return False
        data = image_bytes(
            image, image_format="WEBP", quality=quality,
            max_dimension=max_dimension)
    except ValidationError:
        # Let the field's validators produce the user-facing message.
        return False
    from django.core.files.base import ContentFile

    setattr(instance, field_name, ContentFile(
        data, name=f"{safe_stem(field.name, 'photo')}.webp"))
    return True


# --- documents ------------------------------------------------------------

_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"
_IMAGE_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"RIFF")


def validate_document(upload, *, max_bytes=DOCUMENT_MAX_BYTES, label="document"):
    """Validate a non-image document by extension, size and basic signature."""
    if not upload:
        return
    size = _size(upload)
    if size <= 0:
        raise ValidationError(f"Choose a {label} file first (FILE-REJECTED).")
    if size > max_bytes:
        raise ValidationError(
            f"Input problem: The {label} is {_readable_size(size)}. "
            f"The limit is {_human_size(max_bytes)} (FILE-REJECTED)."
        )
    extension = _extension(getattr(upload, "name", ""))
    if not extension:
        raise ValidationError(
            f"Give the {label} a file extension, such as .pdf or .docx (FILE-REJECTED)."
        )
    if extension in DANGEROUS_EXTENSIONS:
        raise ValidationError(
            f".{extension} files are not accepted. "
            "Upload a PDF, image or office document (FILE-REJECTED)."
        )
    if extension not in DOCUMENT_EXTENSIONS:
        raise ValidationError(
            "Upload a PDF, image, Word, Excel, PowerPoint, CSV or text file (FILE-REJECTED)."
        )

    _rewind(upload)
    try:
        head = upload.read(512)
    finally:
        _rewind(upload)
    if extension == "pdf" and not head.lstrip().startswith(_PDF_MAGIC):
        raise ValidationError("That file is not a valid PDF.")
    if extension in {"docx", "xlsx", "pptx", "odt", "ods", "zip"}:
        if not head.startswith(_ZIP_MAGIC):
            raise ValidationError("That office file is damaged or not valid.")
    if extension in {"png", "jpg", "jpeg", "webp"}:
        # An image uploaded as a document must still be a real image.
        open_image(upload, max_bytes=max_bytes, min_side=0, label=label)


def document_upload_validator(upload):
    """Model/form validator for a FileField holding a document."""
    if not upload:
        return
    validate_document(upload)


def document_upload_to(folder):
    """Build an upload_to callable with a random, extension-preserving name."""
    import uuid

    def upload_to(instance, filename):
        extension = _extension(filename) or "bin"
        return f"{folder}/{uuid.uuid4().hex}.{extension}"

    return upload_to


# --- storage --------------------------------------------------------------

OBJECT_STORAGE_BACKENDS = {"s3", "r2", "b2", "supabase", "object"}


def storage_uses_object_store(backend):
    return str(backend or "").strip().lower() in OBJECT_STORAGE_BACKENDS


def validate_storage_configuration(*, debug, testing, backend):
    """Fail a production boot that would write uploads to an ephemeral disk.

    Local disk is only allowed while developing or running the test suite.
    Render (and every other container host) throws the filesystem away on the
    next deploy, so a production deployment must use an S3-compatible bucket.
    """
    if debug or testing:
        return
    if not storage_uses_object_store(backend):
        raise ImproperlyConfigured(
            "Production uploads must use S3-compatible object storage. Set "
            "STORAGE_BACKEND to s3, r2, b2 or supabase and provide "
            "AWS_STORAGE_BUCKET_NAME, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY "
            "and AWS_S3_ENDPOINT_URL. Local disk is for development only."
        )


def media_url(name):
    """The public or signed URL of a stored file, or an empty string."""
    if not name:
        return ""
    from django.core.files.storage import default_storage

    try:
        return default_storage.url(name)
    except Exception:  # pragma: no cover - a broken storage must not 500 a page
        return ""
