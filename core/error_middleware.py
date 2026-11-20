"""Attach a request id, classify failures and record them exactly once.

This middleware sits outside the error handlers. ``process_exception`` sees the
original exception while it is still available; ``__call__`` also catches error
statuses returned directly by a view (403, 429, 400 and so on).
"""

import time

from django.core.exceptions import PermissionDenied
from django.http import HttpResponse

from core.errors import (
    classify_exception,
    log_error,
    record_error,
    request_id_for,
)

_SKIP_PATHS = ("/static/", "/media/", "/health/", "/favicon.ico")
_PUBLIC_PAGES = {"/", "/login/", "/offline/"}
_SUSPENSION_ALLOWED = (
    "/billing/contact/", "/logout/", "/platform/", "/admin/", "/errors/catalogue.js",
    "/static/", "/media/", "/health/", "/sw.js", "/offline/",
)


class ErrorContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.error_started = time.monotonic()
        request_id_for(request)
        response = None
        try:
            self._check_suspension(request)
            response = self.get_response(request)
            return response
        finally:
            request.error_duration = time.monotonic() - request.error_started
            if response is not None:
                self._finish(request, response)

    def process_exception(self, request, exception):
        spec = classify_exception(exception, request)
        request._error_exception = exception
        request._error_code = spec.code
        record = record_error(
            request, spec, spec.default_status, exception, spec.explanation
        )
        log_error(
            request,
            spec,
            spec.default_status,
            exception,
            reference=getattr(record, "reference", "") if record else "",
        )
        return None  # let Django render the configured error page

    def _finish(self, request, response):
        if not isinstance(response, HttpResponse):
            return
        request_id = request_id_for(request)
        if not response.has_header("X-Request-ID"):
            response["X-Request-ID"] = request_id
        status = response.status_code
        user = getattr(request, "user", None)
        anonymous = user is None or not getattr(user, "is_authenticated", False)
        if (
            status == 200
            and getattr(request, "method", "GET") == "GET"
            and anonymous
            and (getattr(request, "path", "") or "") in _PUBLIC_PAGES
            and not response.has_header("Set-Cookie")
        ):
            # The service worker may cache only pages marked this way. Signed-in
            # pages never get the header, so no private HTML can be replayed.
            response["X-EduSphere-Offline-Public"] = "1"
            if not response.has_header("Cache-Control"):
                response["Cache-Control"] = "public, max-age=300"
        if status < 400 or self._skip(request):
            return
        spec = classify_exception(
            getattr(request, "_error_exception", None), request, status=status
        )
        request._error_code = spec.code
        if getattr(request, "_error_record", None) is None:
            record = record_error(request, spec, status, None, spec.explanation)
            log_error(
                request, spec, status, None,
                reference=getattr(record, "reference", "") if record else "",
            )
        if not response.has_header("X-EduSphere-Error-Code"):
            response["X-EduSphere-Error-Code"] = spec.code

    @staticmethod
    def _check_suspension(request):
        """Suspended institutions get ORG-SUSPENDED, except on billing/platform."""
        path = getattr(request, "path", "") or ""
        if any(path.startswith(prefix) for prefix in _SUSPENSION_ALLOWED):
            return
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return
        if getattr(user, "is_superuser", False) or getattr(user, "is_platform_admin", False):
            return
        institution = getattr(user, "institution", None)
        if institution is None:
            return
        if not (
            getattr(institution, "suspended_at", None)
            or getattr(institution, "is_active", True) is False
        ):
            return
        raise PermissionDenied(
            "Institution suspended (ORG-SUSPENDED). Ask your school's billing "
            "contact to visit /billing/contact/."
        )

    @staticmethod
    def _skip(request):
        path = getattr(request, "path", "") or ""
        return any(path.startswith(prefix) for prefix in _SKIP_PATHS)
