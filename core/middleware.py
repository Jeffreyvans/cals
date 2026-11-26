"""Resolve the institution for this request and make it the current one.

The institution comes from the signed-in user, or from the subdomain, or from
a path prefix such as ``/s/acme/``. When nothing matches, the default
institution is used.
"""

import logging
import time

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.shortcuts import redirect, resolve_url

from core.tenancy import default_institution_id, reset_current_institution, set_current_institution

logger = logging.getLogger("edusphere.slow")

_SKIP_METRICS = ("/static/", "/media/", "/health/", "/favicon.ico")


def _append_vary(response, value):
    existing = response.get("Vary", "")
    parts = [part.strip() for part in existing.split(",") if part.strip()]
    if value not in parts:
        parts.append(value)
        response["Vary"] = ", ".join(parts)


class CustomDomainHostMiddleware:
    """Allow only verified custom domains through Django's Host validation.

    This must run before SecurityMiddleware: SSL redirects call
    ``request.get_host()`` and would otherwise reject a newly verified domain
    before a later middleware could register it.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        raw_host = request.META.get("HTTP_HOST", "") or request.META.get("SERVER_NAME", "")
        host = str(raw_host).split(":", 1)[0].strip(".").lower()
        allowed = {str(value).lower() for value in settings.ALLOWED_HOSTS or []}
        # Writes to domains, modules, plans and subscriptions refresh the list
        # through signals. Only an unknown host can be a newly verified domain
        # that this process has not seen yet, so established hosts skip the
        # lookup entirely.
        if host and "*" not in allowed and host not in allowed:
            try:
                from core.hosts import refresh_dynamic_hosts

                refresh_dynamic_hosts()
            except Exception:
                # Host validation still works from the static environment list.
                # A database outage here must not take down the whole app.
                logger.exception("Could not refresh verified custom domains")
        return self.get_response(request)


def institution_from_request(request):
    """The institution this request belongs to, or None."""
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        institution = getattr(user, "institution", None)
        if institution is not None:
            return institution

    host = request.get_host().split(":")[0].strip(".").lower()
    if host:
        from core.cache import institution_by_domain

        domain_institution = institution_by_domain(host)
        if domain_institution is not None:
            return domain_institution

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


class OldDomainRedirectMiddleware:
    """Permanently redirect a configured old domain to the current SITE_URL.

    After a domain or service rename, the old host keeps its DNS entry and this
    middleware sends a 301 with the same path and query string to the new host.
    Nothing happens unless ``OLD_SITE_URL`` is set.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        old_hosts = {
            str(host).strip().lower().split(":", 1)[0]
            for host in getattr(settings, "OLD_SITE_URL", [])
            if str(host).strip()
        }
        if old_hosts:
            # Read the raw Host header instead of request.get_host(): an old
            # domain is deliberately not in ALLOWED_HOSTS, so get_host() would
            # raise DisallowedHost before we can redirect it. The only value
            # used for the redirect is the configured SITE_URL.
            raw_host = request.META.get("HTTP_HOST", "")
            host = raw_host.split(":", 1)[0].strip(".").lower()
            if host in old_hosts:
                target = getattr(settings, "SITE_URL", "").rstrip("/")
                if target:
                    query = request.META.get("QUERY_STRING", "")
                    destination = f"{target}{request.path}"
                    if query:
                        destination = f"{destination}?{query}"
                    return redirect(destination, permanent=True)
        return self.get_response(request)


class FirstSignInSetupMiddleware:
    """Open the guided setup after a first sign-in, whichever login was used.

    The branded institution login already redirects straight there. This
    narrow check covers the ordinary ``/login/`` screen too: the signal sets a
    one-shot session flag, and the first visit to the dashboard consumes it.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (
            user is not None
            and user.is_authenticated
            and request.session.get("needs_institution_setup")
            and request.path == resolve_url(settings.LOGIN_REDIRECT_URL)
        ):
            request.session.pop("needs_institution_setup", None)
            request.session.modified = True
            return redirect("institution_setup")
        return self.get_response(request)


class HostSessionIsolationMiddleware:
    """Bind a signed-in session to the host where it was established.

    A session cookie sent to another tenant's host is discarded instead of
    being allowed to inherit that tenant's data. Enterprise selectors are also
    cleared, so a group, sponsor or reseller choice can never follow a user to
    a different domain.
    """

    SESSION_KEY = "edusphere_bound_host"
    SELECTOR_KEYS = (
        "group_campus_id", "group_slug", "sponsor_account_id", "sponsor_next",
        "reseller_account_id", "reseller_client_id",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _host(request):
        host = ""
        try:
            host = request.get_host()
        except Exception:
            host = (
                request.META.get("HTTP_HOST", "")
                or request.META.get("SERVER_NAME", "")
            )
        host = str(host).split(":", 1)[0].strip(".").lower()
        if host:
            return host
        # A bare HttpRequest (for example the one used by force_login) has no
        # host header. Use the deployment's first concrete host so the binding
        # matches the next real request instead of being written twice.
        for allowed in getattr(settings, "ALLOWED_HOSTS", []) or []:
            allowed = str(allowed).strip().lower()
            if allowed and allowed != "*":
                return allowed.split(":", 1)[0].strip(".")
        return "localhost"

    def __call__(self, request):
        session = getattr(request, "session", None)
        if session is None:
            return self.get_response(request)
        host = self._host(request)
        bound = session.get(self.SESSION_KEY)
        authenticated = bool(getattr(request.user, "is_authenticated", False))
        has_selector = any(key in session for key in self.SELECTOR_KEYS)
        if bound and bound != host and (authenticated or has_selector):
            session.flush()
            request.user = AnonymousUser()
        elif not bound and authenticated:
            session[self.SESSION_KEY] = host
        return self.get_response(request)


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
            response = self.get_response(request)
            # A response rendered on one host must never be reused for another.
            _append_vary(response, "Host")
            return response
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
