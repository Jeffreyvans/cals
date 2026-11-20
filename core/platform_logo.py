"""Platform wordmark/favicon uploads.

The platform logo is optional: bundled SVG placeholders in
``static/img/brand/`` are used until a platform admin uploads the final artwork
from Platform settings. Uploads go through the same safe pipeline as institution
logos (Pillow check, re-encode, random names, SVG refused), so an uploaded SVG
can never reach a browser or a PDF.

Only PNG, JPG and WebP are accepted. SVG is deliberately refused; edit the
checked-in placeholder files to replace the brand artwork.
"""

from __future__ import annotations

import secrets

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from core.logo import open_logo, render_logo
from core.uploads import image_bytes

FOLDER = "platform/logos"
SIZES = (64, 160, 512)


def process_platform_logo(uploaded):
    """Validate and re-encode an upload; return (paths, preview_bytes).

    ``paths`` maps the rendered size to a stored file name. The caller records
    the 512 px WebP path in ``PlatformSetting`` and clears the brand cache.
    """
    image = open_logo(uploaded)
    token = secrets.token_hex(8)
    paths = {}
    for size in SIZES:
        name = f"{FOLDER}/{token}_{size}.webp"
        data = image_bytes(render_logo(image, size), image_format="WEBP", quality=88)
        paths[str(size)] = default_storage.save(name, ContentFile(data, name=name))
    png_name = f"{FOLDER}/{token}_512.png"
    paths["png"] = default_storage.save(
        png_name,
        ContentFile(image_bytes(render_logo(image, 512), image_format="PNG"), name=png_name),
    )
    return paths


def remove_platform_logo(paths):
    """Delete earlier stored platform logo files after a replacement."""
    for name in set((paths or {}).values()):
        if not name:
            continue
        try:
            default_storage.delete(name)
        except Exception:  # pragma: no cover - a stale file must not stop a save
            pass
