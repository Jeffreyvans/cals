"""Build the responsive landing images from the originals.

Usage:
    python scripts/build_landing_images.py

The originals live in ``static/img/landing/originals``. This script writes
WebP and JPEG versions at the widths the landing page uses, keeping every
file below about 200 KB. Re-run it after replacing an original.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "static" / "img" / "landing" / "originals"
TARGET = ROOT / "static" / "img" / "landing"
MAX_BYTES = 200 * 1024

# source stem -> (widths, alt text is in the template)
IMAGES = {
    "hero-classroom": (640, 1040),
    "audience-primary": (480, 960),
    "audience-secondary": (480, 960),
    "audience-college": (480, 960),
    "audience-vocational": (480, 960),
}


def _resize(image: Image.Image, width: int) -> Image.Image:
    if image.width <= width:
        return image.copy()
    height = round(image.height * (width / image.width))
    return image.resize((width, height), Image.Resampling.LANCZOS)


def _save_under_limit(image: Image.Image, path: Path, fmt: str) -> None:
    quality = 82 if fmt == "JPEG" else 80
    while True:
        save_kwargs = {"format": fmt, "optimize": True}
        if fmt == "JPEG":
            save_kwargs.update(quality=quality, progressive=True, subsampling=2)
        else:
            save_kwargs.update(quality=quality, method=6)
        image.save(path, **save_kwargs)
        if path.stat().st_size <= MAX_BYTES or quality <= 40:
            break
        quality -= 6
    size_kb = path.stat().st_size / 1024
    print(f"  {path.name:38} {image.width}x{image.height}  {size_kb:6.1f} KB")


def main() -> None:
    TARGET.mkdir(parents=True, exist_ok=True)
    missing = []
    for stem, widths in IMAGES.items():
        source = SOURCE / f"{stem}.jpg"
        if not source.exists():
            missing.append(str(source))
            continue
        with Image.open(source) as opened:
            opened.load()
            image = opened.convert("RGB")
        for width in widths:
            resized = _resize(image, width)
            _save_under_limit(
                resized, TARGET / f"{stem}-{width}.webp", "WEBP"
            )
            _save_under_limit(
                resized, TARGET / f"{stem}-{width}.jpg", "JPEG"
            )
    if missing:
        raise SystemExit(
            "Missing originals:\n  " + "\n  ".join(missing)
        )


if __name__ == "__main__":
    main()
