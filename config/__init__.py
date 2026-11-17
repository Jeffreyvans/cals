"""Load the Celery application when Django starts.

Celery is a hard requirement in ``requirements.txt``, but the import is still
guarded so a stripped-down install can boot and run tasks inline.
"""

try:
    from .celery import app as celery_app
except Exception:  # pragma: no cover - Celery missing or misconfigured
    celery_app = None

__all__ = ("celery_app",)
