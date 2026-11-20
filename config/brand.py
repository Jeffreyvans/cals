"""One source of truth for the platform name, publisher and support identity.

A rename is an edit here (or the ``BRAND_NAME`` environment override) plus a
change to the placeholder SVG assets in ``static/img/brand/``. Every template
reads the values through the ``branding`` template tags or the ``PLATFORM_BRAND``
context processor; Python code imports the constants below.

Deliberately NOT renamed: Python packages and Django apps, database tables and
migration files, existing URL routes, ``EDUSPHERE_*`` environment variables,
cache/session keys, logger names and the ``X-EduSphere-*`` header names. Those
are internal identifiers; changing them would break live installations. See the
allow-list in ``config/tests/test_brand.py``.
"""

from __future__ import annotations

import os
from datetime import date


def _env(name: str, default: str = "") -> str:
    """A trimmed, non-blank environment value or ``default``."""
    value = os.environ.get(name, "")
    return value.strip() if value and value.strip() else default


# BRAND_NAME lets a hosted deployment rename the product without a code edit.
# COMPANY_NAME follows the product name so the whole identity changes together.
PRODUCT_NAME = _env("BRAND_NAME", "Vanpere")
COMPANY_NAME = _env("BRAND_COMPANY", f"{PRODUCT_NAME} Digital Solutions")
TAGLINE = "School management in one place"
SUPPORT_PHONE = _env("SUPPORT_PHONE", "+263774573765")
SUPPORT_PHONE_DISPLAY = _env("SUPPORT_PHONE_DISPLAY", "+263 774 573 765")
SUPPORT_EMAIL = _env("SUPPORT_EMAIL", "vanhuvaonejeffrey@gmail.com")
# The website comes from SITE_URL (the older EDUSPHERE_* spelling is a fallback
# environment variable name and stays for existing deployments).
WEBSITE = _env("SITE_URL", _env("EDUSPHERE_SITE_URL", "http://localhost:8000")).rstrip("/")
COPYRIGHT = f"\u00a9 {date.today().year} {COMPANY_NAME}. All rights reserved."
POWERED_BY = f"Powered by {PRODUCT_NAME}"
VIA_BRAND = f"via {PRODUCT_NAME}"
# HTTP User-Agent tokens cannot contain spaces, so normalise the product name.
WEBHOOK_USER_AGENT = f"{PRODUCT_NAME.replace(' ', '-')}-Webhooks/1.0"

# Placeholder identity files. Drop the final approved artwork at these paths.
LOGO_PUBLIC = "img/brand/vanpere-wordmark-brown.svg"
LOGO_APP = "img/brand/vanpere-wordmark-green.svg"
LOGO_MARIGOLD = "img/brand/vanpere-wordmark-marigold.svg"
FAVICON = "img/brand/vanpere-favicon.svg"

# Theme colours (brown public site, green + marigold signed-in app).
COLOUR_BROWN = "#4E342E"
COLOUR_GREEN = "#1B5E3F"
COLOUR_MARIGOLD = "#E9A23B"
COLOUR_PUBLIC = COLOUR_BROWN
COLOUR_APP = COLOUR_GREEN

_VALUES = {
    "product_name": PRODUCT_NAME,
    "company_name": COMPANY_NAME,
    "tagline": TAGLINE,
    "support_phone": SUPPORT_PHONE,
    "support_phone_display": SUPPORT_PHONE_DISPLAY,
    "support_email": SUPPORT_EMAIL,
    "website": WEBSITE,
    "copyright": COPYRIGHT,
    "powered_by": POWERED_BY,
    "via_brand": VIA_BRAND,
    "logo_public": LOGO_PUBLIC,
    "logo_app": LOGO_APP,
    "logo_marigold": LOGO_MARIGOLD,
    "favicon": FAVICON,
    "colour_brown": COLOUR_BROWN,
    "colour_green": COLOUR_GREEN,
    "colour_marigold": COLOUR_MARIGOLD,
    "colour_public": COLOUR_PUBLIC,
    "colour_app": COLOUR_APP,
}


def values() -> dict:
    """Brand values for templates, including runtime overrides.

    ``SITE_URL`` is resolved from Django settings when available so links in
    emails and PDFs use the configured public URL. A logo uploaded in platform
    settings replaces the bundled placeholder when present.
    """
    result = dict(_VALUES)
    try:
        from django.conf import settings

        if getattr(settings, "SITE_URL", ""):
            result["website"] = settings.SITE_URL.rstrip("/")
    except Exception:  # pragma: no cover - settings may not be configured yet
        pass
    result.update(_uploaded_logos())
    return result


_UPLOADED_LOGO_KEYS = ("logo_public", "logo_app", "favicon")
_UPLOADED_LOGO_CACHE_PREFIX = "vanpere.brand."


def _uploaded_logos() -> dict:
    """Uploaded logo URLs, fetched with one query when the cache is cold.

    A page renders this several times (head, header, footer, email wrappers), so
    every lookup must be cached. Missing keys are read together to keep a cold
    request to a single database query.
    """
    try:
        from django.core.cache import cache

        cached = cache.get_many(
            [_UPLOADED_LOGO_CACHE_PREFIX + name for name in _UPLOADED_LOGO_KEYS]
        )
        missing = [
            name for name in _UPLOADED_LOGO_KEYS
            if _UPLOADED_LOGO_CACHE_PREFIX + name not in cached
        ]
        if missing:
            from platform_admin.models import PlatformSetting

            rows = PlatformSetting.objects.filter(
                key__in=[f"brand_{name}" for name in missing]
            )
            by_key = {row.key: row for row in rows}
            from core.uploads import media_url

            for name in missing:
                row = by_key.get(f"brand_{name}")
                path = (row.value or {}).get("value", "") if row else ""
                cached[_UPLOADED_LOGO_CACHE_PREFIX + name] = media_url(path) if path else ""
            cache.set_many(cached, 300)
        return {
            name: cached.get(_UPLOADED_LOGO_CACHE_PREFIX + name, "")
            for name in _UPLOADED_LOGO_KEYS
        }
    except Exception:  # pragma: no cover - no database during startup/migrations
        return {}


def clear_logo_cache() -> None:
    """Forget uploaded logo URLs after a platform settings save."""
    try:
        from django.core.cache import cache

        cache.delete_many(
            [_UPLOADED_LOGO_CACHE_PREFIX + name for name in _UPLOADED_LOGO_KEYS]
        )
    except Exception:  # pragma: no cover
        pass


def context(request=None) -> dict:
    """Template context: ``PLATFORM_BRAND`` plus convenient flat names."""
    data = values()
    result = {"PLATFORM_BRAND": data}
    for key, value in data.items():
        result["BRAND_" + key.upper()] = value
    result["PRODUCT_NAME"] = data["product_name"]
    result["COMPANY_NAME"] = data["company_name"]
    return result


def brand_context(request):
    """Context processor registered in ``config.settings.TEMPLATES``."""
    return context(request)
