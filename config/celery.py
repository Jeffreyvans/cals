"""Celery application for the platform.

Start a worker with::

    celery -A config worker --loglevel=info --concurrency=2

The broker and result backend come from ``CELERY_BROKER_URL`` (or
``REDIS_URL``) in the environment. With no broker the project runs tasks
inline and this module is harmless.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("edusphere")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
