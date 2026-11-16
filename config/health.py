"""A tiny liveness endpoint for Render and other load balancers.

``/health/`` must stay cheap and unauthenticated. It runs one trivial query so
a deployment is only marked healthy once the database connection works.
"""

import logging

from django.db import connection
from django.http import JsonResponse

logger = logging.getLogger(__name__)


def health_check(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        logger.exception("Health check could not reach the database")
        response = JsonResponse(
            {"status": "error", "database": "error"}, status=503
        )
    else:
        response = JsonResponse({"status": "ok", "database": "ok"})
    response["Cache-Control"] = "no-store"
    return response
