"""Resolve the institution for this request and make it the current one.

The institution comes from the signed-in user, or from the subdomain, or from
a path prefix such as ``/s/acme/``. When nothing matches, the default
institution is used.
"""

import logging
import time

from django.conf import settings
from django.core.cache import cache

from core.tenancy import default_institution_id, reset_current_institution, set_current_institution

logger = logging.getLogger("edusphere.slow")

_SKIP_METRICS = ("/static/", "/media/", "/health/", "/favicon.ico")


def _append_vary(response, value):
    existing = response.get("Vary", "")
    parts = [part.strip() for part in existing.split(",") if part.strip()]
    if value not in parts:
        parts.append(value)
        response["Vary"] = ", ".join(parts)


def institution_from_request(request):
    """The institution this request belongs to, or None."""
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        institution = getattr(user, "institution", None)
        if institution is not None:
            return institution

    host = request.get_host().split(":")[0]
    parts = host.split(".")
    if len(parts) > 2:
        from core.cache import institution_by_slug

        inst = institution_by_slug(parts[0].lower())
        if inst is not None:
            return inst

    path = request.path.strip("/")
    if path:
        prefix = path.split("/")[0]
        if prefix:
            from core.cache import institution_by_slug

            inst = institution_by_slug(prefix.lower())
            if inst is not None:
                return inst
    return None


class InstitutionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        institution = institution_from_request(request)
        request.institution = institution
        # Always scope the request explicitly. Public pages have no institution
        # of their own, so they read the installation's default institution
        # rather than every institution.
        token = set_current_institution(
            institution if institution is not None else default_institution_id()
        )
        try:
            return self.get_response(request)
        finally:
            reset_current_institution(token)


class LowDataMiddleware:
    """Remember a person's low-data choice and expose it to templates.

    A phone on a metered connection can send ``Save-Data: on`` (Chrome and
    Opera do this automatically) or follow a link with ``?low_data=1``. The
    templates then skip charts and large pictures, and the service worker
    serves the small cached shell instead of downloading the hero image.
    """

    COOKIE = "edusphere_low_data"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        requested = request.GET.get("low_data", "").lower()
        cookie = request.COOKIES.get(self.COOKIE, "")
        save_data = request.META.get("HTTP_SAVE_DATA", "").lower()
        low = requested in {"1", "on", "true", "yes"} or cookie == "1" or save_data == "on"
        if requested in {"0", "off", "false", "no"}:
            low = False
        request.low_data = low
        response = self.get_response(request)
        if requested:
            response.set_cookie(
                self.COOKIE,
                "1" if low else "0",
                max_age=365 * 24 * 3600,
                samesite="Lax",
                secure=request.is_secure(),
            )
        if low:
            _append_vary(response, "Save-Data")
            _append_vary(response, "Cookie")
        return response


class ResponseMetricsMiddleware:
    """Log slow pages and remember their size for the measurement command.

    This is intentionally tiny: one clock read, one length lookup and (only
    for HTML pages) one small cache write. It never reads the database.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = time.monotonic()
        response = self.get_response(request)
        duration = time.monotonic() - started
        path = request.path or "/"

        if not response.has_header("Server-Timing"):
            response["Server-Timing"] = f"app;dur={duration * 1000:.0f}"

        if duration >= getattr(settings, "SLOW_REQUEST_SECONDS", 1.5):
            logger.warning(
                "Slow request %s %s took %.2fs status=%s",
                request.method,
                path,
                duration,
                response.status_code,
            )

        if not path.startswith(_SKIP_METRICS) and response.status_code == 200:
            try:
                size = len(response.content)
            except (AttributeError, TypeError):
                size = 0
            if size:
                key = f"edusphere:metrics:{path}"
                row = cache.get(key) or {"count": 0, "bytes": 0, "ms": 0.0}
                row["count"] += 1
                row["bytes"] += size
                row["ms"] += duration * 1000
                cache.set(key, row, 24 * 3600)
        return response
