"""Classification, recording and rendering of every EduSphere error.

The single entry point is :func:`classify_exception`. Views, API handlers,
middleware and logs all call it so the same category and code reach the screen,
the JSON response and the Errors page.
"""

from __future__ import annotations

import logging
import re
import secrets
import time
from urllib.parse import urlsplit

from django.core.exceptions import (
    ObjectDoesNotExist,
    PermissionDenied,
    SuspiciousOperation,
    ValidationError,
)
from django.db import DatabaseError, IntegrityError, OperationalError, transaction
from django.http import Http404, JsonResponse
from django.shortcuts import render

from core.access import user_has_code
from core.error_catalogue import CATEGORY_LABELS, code_for_status, get_spec

logger = logging.getLogger("edusphere.errors")

_SAFE_HEADER = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SUPPORT_PHONE = "+263774573765"
_SUPPORT_EMAIL = "vanhuvaonejeffrey@gmail.com"


def support_contacts():
    """The two support contacts shown on every error surface."""
    return {"phone": _SUPPORT_PHONE, "email": _SUPPORT_EMAIL}


def support_context(request):
    """Template context processor so inline errors can show support details."""
    return {"support": support_contacts()}


def request_id_for(request):
    """Use a trusted incoming request id, otherwise make one for this request."""
    existing = getattr(request, "error_request_id", None)
    if existing:
        return existing
    supplied = request.META.get("HTTP_X_REQUEST_ID", "") if request else ""
    value = supplied if supplied and _SAFE_HEADER.fullmatch(supplied) else secrets.token_hex(8)
    request.error_request_id = value
    return value


def view_name_for(request):
    match = getattr(request, "resolver_match", None)
    if match is not None:
        return match.view_name or match.url_name or ""
    return ""


def _view_hint(exc, request):
    """A lower-case hint from the failing view or service, never user input."""
    parts = [view_name_for(request)]
    cls = type(exc)
    parts.append(f"{cls.__module__}.{cls.__name__}")
    if request is not None:
        parts.append(getattr(request, "path", "") or "")
    return " ".join(parts).lower()


def _class_name(exc):
    cls = type(exc)
    return f"{cls.__module__}.{cls.__qualname__}" if exc is not None else ""


def _status_of(exc, status=None):
    if status is not None:
        return int(status)
    value = getattr(exc, "status_code", None)
    if isinstance(value, int):
        return value
    if isinstance(exc, Http404):
        return 404
    if isinstance(exc, PermissionDenied):
        return 403
    if isinstance(exc, ValidationError):
        return 400
    if isinstance(exc, DatabaseError):
        return 503
    return 500


def _suspended_institution(request):
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    institution = getattr(request, "institution", None) or getattr(user, "institution", None)
    if institution is None:
        return False
    return bool(
        getattr(institution, "suspended_at", None)
        or getattr(institution, "is_active", True) is False
    )


def classify_exception(exc, request=None, status=None):
    """Map an exception (and optional status) to the shared ErrorSpec.

    ``request`` is used only for safe hints such as the view name and the
    institution's suspension flag. No request data or exception text is ever
    copied into the returned catalogue entry.
    """
    status_code = _status_of(exc, status)
    hint = _view_hint(exc, request)
    text = str(exc).lower() if exc is not None else ""

    # CSRF and SuspiciousFileOperation are subclasses of SuspiciousOperation;
    # check the specific cases before the general one.
    if "suspiciousfileoperation" in hint or "suspiciousfileoperation" in type(exc).__name__.lower():
        return get_spec("FILE-REJECTED")
    if "csrf" in text or "csrf" in hint:
        return get_spec("CSRF")
    if isinstance(exc, SuspiciousOperation):
        return get_spec("INPUT-INVALID")

    # Django database exceptions.
    try:
        from django.db.models.deletion import ProtectedError
    except ImportError:  # pragma: no cover - present in supported Django
        ProtectedError = ()
    if isinstance(exc, ProtectedError):
        return get_spec("DB-PROTECTED")
    if isinstance(exc, OperationalError) or "operationalerror" in hint or "databaseerror" in hint:
        return get_spec("DB-DOWN")
    if isinstance(exc, IntegrityError) or "integrityerror" in hint:
        return get_spec("DB-CONFLICT")
    if isinstance(exc, DatabaseError):
        return get_spec("DB-DOWN")

    # Access failures. A suspended school overrides the generic denial so the
    # message points at the billing contact page.
    if isinstance(exc, Http404) or isinstance(exc, ObjectDoesNotExist):
        return get_spec("NOT-FOUND")
    no_auth_code = getattr(exc, "code", None)
    if no_auth_code == "invalid_login" or "authenticationfailed" in hint:
        return get_spec("AUTH-FAILED")
    if "notauthenticated" in hint or "authenticationrequired" in hint:
        return get_spec("AUTH-REQUIRED")
    if "tokenexpired" in hint or "sessionexpired" in hint or "session_expired" in text:
        return get_spec("AUTH-EXPIRED")
    if "unverified" in text or "email_not_verified" in hint:
        return get_spec("AUTH-UNVERIFIED")
    if "twofa" in hint or "2fa" in hint or "one-time code" in text:
        return get_spec("AUTH-2FA")
    if isinstance(exc, PermissionDenied):
        if _suspended_institution(request) or "suspended" in text or "org-suspended" in hint:
            return get_spec("ORG-SUSPENDED")
        return get_spec("PERM-DENIED")
    if "locked" in text or "locked_until" in hint:
        return get_spec("AUTH-LOCKED")

    # Validation and uploads.
    if isinstance(exc, ValidationError):
        if "import" in hint or "import" in text or "spreadsheet" in text:
            return get_spec("IMPORT-ROWS")
        if re.search(r"\b(file|files|upload|logo|extension|bytes|mb)\b", text):
            return get_spec("FILE-REJECTED")
        return get_spec("INPUT-INVALID")
    if "importerror" in hint and "upload" in hint:
        return get_spec("IMPORT-ROWS")
    if any(word in text for word in ("too large", "file type", "unsupported extension")):
        return get_spec("FILE-REJECTED")

    # External providers. A timeout still names the provider when the view or
    # the exception class tells us which one failed.
    if "gatewayerror" in hint or "paynow" in hint:
        return get_spec("EXT-PAY")
    if "fiscal" in hint:
        return get_spec("EXT-FISCAL")
    if "webhook" in hint or "webhookdelivery" in hint:
        return get_spec("EXT-HOOK")
    if "storage" in hint or "s3" in hint or "boto" in hint:
        return get_spec("EXT-STORAGE")
    if "smtp" in hint or "anymail" in hint or "email" in hint:
        return get_spec("EXT-EMAIL")
    try:
        import requests
    except ImportError:  # pragma: no cover - requests is a project dependency
        requests = None
    if requests is not None:
        if isinstance(exc, requests.exceptions.Timeout):
            return get_spec("NET-TIMEOUT")
        if isinstance(exc, requests.exceptions.ConnectionError):
            return get_spec("NET-UNREACHABLE")

    if "throttled" in hint:
        return get_spec("SRV-429")
    if status_code == 401:
        return get_spec("AUTH-REQUIRED")
    return get_spec(code_for_status(status_code))


def can_view_error_details(user):
    """Only platform admins and staff granted errors.view_details see internals."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False) or getattr(user, "is_platform_admin", False):
        return True
    if user_has_code(user, "errors.view_details"):
        return True
    staff = getattr(user, "platform_staff", None)
    if staff is None:
        try:
            from platform_admin.services import platform_staff_for

            staff = platform_staff_for(user)
        except ImportError:  # pragma: no cover
            staff = None
    if staff is None:
        return False
    from platform_admin.permissions import has_code

    return has_code(staff, "errors.view_details")


def _reference():
    return f"ERR-{secrets.token_hex(3).upper()}"


def record_error(request, spec, status=None, exception=None, detail="", duration=None):
    """Create (or reuse) the database record for one failed request.

    Only trusted metadata is stored: never the request body, headers, stack
    trace, SQL or exception text. A failure to record is swallowed so an error
    page can still be shown when the database itself is the problem.
    """
    existing = getattr(request, "_error_record", None) if request is not None else None
    if existing is not None:
        return existing
    record = None
    try:
        from core.models import ErrorRecord

        user = getattr(request, "user", None)
        user_id = getattr(user, "pk", None) if getattr(user, "is_authenticated", False) else None
        institution = getattr(request, "institution", None)
        if institution is None and user is not None and getattr(user, "is_authenticated", False):
            institution = getattr(user, "institution", None)
        duration_value = duration
        if duration_value is None:
            started = getattr(request, "error_started", None)
            duration_value = time.monotonic() - started if started is not None else 0.0
        for _ in range(4):
            try:
                with transaction.atomic():
                    record = ErrorRecord.objects.create(
                        reference=_reference(),
                        category=spec.category,
                        code=spec.code,
                        status=int(status or spec.default_status),
                        request_id=request_id_for(request),
                        view_name=view_name_for(request),
                        method=getattr(request, "method", "") or "",
                        path=(urlsplit(getattr(request, "path", "") or "")).path[:500],
                        duration=max(0.0, float(duration_value)),
                        exception_class=_class_name(exception)[:255],
                        service=spec.service,
                        user_id=user_id,
                        institution_id=getattr(institution, "pk", None),
                        title=spec.title,
                        explanation=spec.explanation,
                        next_steps=list(spec.steps),
                        detail=(detail or spec.explanation)[:1000],
                    )
                break
            except IntegrityError:
                continue
        if request is not None:
            request._error_record = record
    except Exception:  # pragma: no cover - logging must never break an error page
        logger.warning("Could not store error record for %s", spec.code, exc_info=False)
    return record


def log_error(request, spec, status=None, exception=None, duration=None, reference=""):
    """Write one structured log line with every field support needs."""
    if request is not None and getattr(request, "_error_logged", False):
        return
    if request is not None:
        request._error_logged = True
    user = getattr(request, "user", None)
    user_id = getattr(user, "pk", None) if getattr(user, "is_authenticated", False) else None
    institution = getattr(request, "institution", None)
    if institution is None and user is not None and getattr(user, "is_authenticated", False):
        institution = getattr(user, "institution", None)
    if duration is None:
        started = getattr(request, "error_started", None)
        duration = time.monotonic() - started if started is not None else 0.0
    status_code = int(status or spec.default_status)
    logger.error(
        "%s %s status=%s category=%s code=%s reference=%s request_id=%s "
        "view=%s duration=%.3fs user_id=%s institution_id=%s exception=%s",
        getattr(request, "method", "GET") or "GET",
        getattr(request, "path", "/") or "/",
        status_code,
        spec.category,
        spec.code,
        reference,
        request_id_for(request),
        view_name_for(request) or "-",
        duration,
        user_id if user_id is not None else "-",
        getattr(institution, "pk", None) if institution is not None else "-",
        _class_name(exception) or "-",
        extra={
            "category": spec.category,
            "code": spec.code,
            "request_id": request_id_for(request),
            "view_name": view_name_for(request),
            "duration_ms": round(float(duration) * 1000, 1),
            "user_id": user_id,
            "institution_id": getattr(institution, "pk", None),
            "reference": reference,
            "http_status": status_code,
        },
    )


def safe_detail(value, limit=500):
    """Reuse the email sanitizer so no token, password or key is ever shown."""
    from core.notifications import sanitize_detail

    return sanitize_detail(value, limit=limit)


def error_context(request, spec, record=None, detail="", status=None):
    """Build the shared template/log/JSON context for a classified error."""
    started = getattr(request, "error_started", None)
    duration = time.monotonic() - started if started is not None else 0.0
    status_code = int(status or spec.default_status)
    show_details = can_view_error_details(getattr(request, "user", None))
    return {
        "error": spec.payload(),
        "error_category": spec.category,
        "error_category_label": CATEGORY_LABELS[spec.category],
        "error_code": spec.code,
        "error_icon": spec.icon,
        "error_title": spec.headline,
        "error_explanation": spec.explanation,
        "error_action": spec.action,
        "error_steps": list(spec.steps),
        "error_fixed_by": spec.fixed_by,
        "error_status": status_code,
        "error_request_id": request_id_for(request),
        "error_reference": getattr(record, "reference", "") or "ERR-PENDING",
        "error_time": getattr(record, "created", None),
        "error_view_name": view_name_for(request),
        "error_duration": duration,
        "error_exception_class": _class_name(getattr(request, "_error_exception", None)) if show_details else "",
        "error_service": spec.service if show_details else "",
        "show_error_details": show_details,
        "support": support_contacts(),
        "error_retry_url": _retry_url(request),
        "error_detail": safe_detail(detail) if detail else "",
    }


def _retry_url(request):
    if request is None:
        return "/"
    path = getattr(request, "path", "") or "/"
    if getattr(request, "method", "GET") != "GET":
        return path
    query = getattr(request, "META", {}).get("QUERY_STRING", "")
    return f"{path}?{query}" if query else path


def render_error(request, code, status=None, exception=None, detail="", template="errors/page.html"):
    """Render the full error page and record/log the failure once."""
    spec = get_spec(code)
    status_code = int(status or spec.default_status)
    if exception is not None:
        request._error_exception = exception
    record = record_error(request, spec, status_code, exception, detail)
    log_error(
        request, spec, status_code, exception,
        reference=getattr(record, "reference", "") if record else "",
    )
    return render(
        request,
        template,
        error_context(request, spec, record, detail, status_code),
        status=status_code,
    )


def error_json(request, code, status=None, exception=None, detail="", fields=None):
    """The same classified message in a JSON body, for hand-written API views."""
    spec = get_spec(code)
    status_code = int(status or spec.default_status)
    if exception is not None:
        request._error_exception = exception
    record = record_error(request, spec, status_code, exception, detail)
    log_error(
        request, spec, status_code, exception,
        reference=getattr(record, "reference", "") if record else "",
    )
    payload = error_context(request, spec, record, detail, status_code)
    body = {
        "detail": spec.message,
        "error": spec.code,
        "category": spec.category,
        "category_label": spec.category_label,
        "code": spec.code,
        "title": spec.title,
        "headline": spec.headline,
        "explanation": spec.explanation,
        "action": spec.action,
        "next_steps": list(spec.steps),
        "fixed_by": spec.fixed_by,
        "icon": spec.icon,
        "reference": payload["error_reference"],
        "request_id": payload["error_request_id"],
        "http_status": status_code,
        "time": payload["error_time"].isoformat() if payload["error_time"] else "",
        "support": support_contacts(),
    }
    if fields is not None:
        body["fields"] = fields
    if payload["show_error_details"]:
        body["technical"] = {
            "code": spec.code,
            "http_status": status_code,
            "request_id": payload["error_request_id"],
            "time": body["time"],
            "exception_class": payload["error_exception_class"],
            "view_name": payload["error_view_name"],
            "failing_service": payload["error_service"],
        }
    response = JsonResponse(body, status=status_code)
    response["X-Request-ID"] = payload["error_request_id"]
    response["X-EduSphere-Error-Code"] = spec.code
    return response


def api_exception_handler(exc, context):
    """DRF exception handler that keeps the default shape and adds our fields."""
    from rest_framework.views import exception_handler as drf_exception_handler

    response = drf_exception_handler(exc, context)
    if response is None:
        return None
    request = context.get("request")
    status_code = response.status_code
    spec = classify_exception(exc, request, status=status_code)
    if request is not None:
        request._error_exception = exc
    record = record_error(request, spec, status_code, exc, spec.explanation)
    log_error(
        request, spec, status_code, exc,
        reference=getattr(record, "reference", "") if record else "",
    )
    data = response.data
    fields = data if isinstance(data, dict) and "detail" not in data else None
    payload = error_context(request, spec, record, spec.explanation, status_code)
    body = {
        "detail": spec.message,
        "error": spec.code,
        "category": spec.category,
        "category_label": spec.category_label,
        "code": spec.code,
        "title": spec.title,
        "headline": spec.headline,
        "explanation": spec.explanation,
        "action": spec.action,
        "next_steps": list(spec.steps),
        "fixed_by": spec.fixed_by,
        "icon": spec.icon,
        "reference": payload["error_reference"],
        "request_id": payload["error_request_id"],
        "http_status": status_code,
        "time": payload["error_time"].isoformat() if payload["error_time"] else "",
        "support": support_contacts(),
    }
    if fields is not None:
        body["fields"] = fields
    if payload["show_error_details"]:
        body["technical"] = {
            "code": spec.code,
            "http_status": status_code,
            "request_id": payload["error_request_id"],
            "time": body["time"],
            "exception_class": payload["error_exception_class"],
            "view_name": payload["error_view_name"],
            "failing_service": payload["error_service"],
        }
    response.data = body
    response["X-Request-ID"] = payload["error_request_id"]
    response["X-EduSphere-Error-Code"] = spec.code
    return response


def add_error_message(request, code, extra=""):
    """Add a categorised inline message to a server-rendered page."""
    from django.contrib import messages

    spec = get_spec(code)
    text = spec.message
    if extra:
        text = f"{text} {safe_detail(extra)}"
    messages.error(request, text, extra_tags=f"error-code:{spec.code}")
    return spec
