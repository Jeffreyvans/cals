"""Global error handlers and the browser catalogue endpoint."""

import json
import re

from django.http import HttpResponse

from core.error_catalogue import ERROR_CATALOGUE, get_spec
from core.errors import classify_exception, render_error


def error_catalogue_js(request):
    """Serve the one shared catalogue to the browser as an external script."""
    payload = {spec.code: spec.payload() for spec in ERROR_CATALOGUE}
    body = "window.EDUSPHERE_ERRORS = " + json.dumps(payload, ensure_ascii=False) + ";\n"
    response = HttpResponse(body, content_type="application/javascript")
    response["Cache-Control"] = "public, max-age=3600"
    return response


def bad_request(request, exception=None):
    spec = classify_exception(exception, request, status=400)
    return render_error(request, spec.code, 400, exception)


def permission_denied(request, exception=None):
    spec = classify_exception(exception, request, status=403)
    # Only a fixed, administrator-authored hint is safe to pass through.
    hint = ""
    match = re.search(r"Ask your school administrator[^.]*\.", str(exception or ""))
    if match:
        hint = match.group(0)
    match = re.search(r"Ask your school's billing contact[^.]*\.", str(exception or ""))
    if match:
        hint = match.group(0)
    return render_error(request, spec.code, 403, exception, detail=hint)


def not_found(request, exception=None):
    return render_error(request, "NOT-FOUND", 404, exception)


def forbidden(request, exception=None):
    """Alias used by older URL configurations."""
    return permission_denied(request, exception)


def server_error(request, exception=None):
    spec = classify_exception(exception, request, status=500)
    return render_error(request, spec.code, 500, exception)


def service_unavailable(request, exception=None):
    spec = get_spec("SRV-503")
    return render_error(request, spec.code, 503, exception)
