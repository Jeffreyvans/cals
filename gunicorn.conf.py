"""Gunicorn settings for the platform.

Render starts the web process with::

    gunicorn config.wsgi:application --config gunicorn.conf.py

The defaults are sized for a small Render instance (512 MB RAM). Raise
``WEB_CONCURRENCY`` and ``GUNICORN_THREADS`` together with the plan; never set
more workers than the database pool can serve. ``max_requests`` recycles a
worker after a thousand requests, which releases any slow leak before it
becomes an outage.
"""

import multiprocessing
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"

# gthread gives each worker a few threads for I/O (email API, object storage,
# database) while keeping memory low. The free plan is happiest with 2 x 2.
workers = int(os.environ.get("WEB_CONCURRENCY", min(4, multiprocessing.cpu_count() * 2 + 1)))
threads = int(os.environ.get("GUNICORN_THREADS", "2"))
worker_class = "gthread"

# A page must never hold a worker for two minutes. Anything longer belongs in
# a Celery job. Keep the graceful timeout short so a deploy is quick.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "60"))
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.environ.get("GUNICORN_KEEPALIVE", "5"))

# Recycle workers to bound memory growth. The jitter stops every worker
# restarting at the same moment.
max_requests = int(os.environ.get("GUNICORN_MAX_REQUESTS", "1000"))
max_requests_jitter = int(os.environ.get("GUNICORN_MAX_REQUESTS_JITTER", "100"))

# Render terminates TLS and forwards the real client address.
forwarded_allow_ips = "*"
proxy_allow_ips = "*"

accesslog = os.environ.get("GUNICORN_ACCESS_LOG", "-")
errorlog = os.environ.get("GUNICORN_ERROR_LOG", "-")
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(L)s'

# Do not preload: every worker opens its own database connections, which is
# what CONN_MAX_AGE expects. Preloading would copy one connection into every
# forked worker and corrupt the pool.
preload_app = False
