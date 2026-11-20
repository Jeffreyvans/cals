"""Structured logging for slow SQL queries.

``SLOW_QUERY_SECONDS`` is documented in the deployment guide; this module makes
it effective. Only SQL text is logged (truncated), never parameters or values,
so passwords and personal data are not written to logs.
"""

import logging
import time

from django.conf import settings
from django.db.backends.signals import connection_created

logger = logging.getLogger("edusphere.slow")


def _on_connection_created(sender, connection, **kwargs):
    threshold = float(getattr(settings, "SLOW_QUERY_SECONDS", 0) or 0)
    if threshold <= 0 or getattr(connection, "_vanpere_slow_wrapper", False):
        return

    def execute_wrapper(execute, sql, params, many, context):
        started = time.perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            elapsed = time.perf_counter() - started
            if elapsed >= threshold:
                summary = " ".join(str(sql).split())[:300]
                logger.info("slow query %.3fs: %s", elapsed, summary)

    connection.execute_wrapper(execute_wrapper)
    connection._vanpere_slow_wrapper = True


def connect():
    connection_created.connect(
        _on_connection_created,
        dispatch_uid="vanpere.core.db_monitor.slow_queries",
    )
