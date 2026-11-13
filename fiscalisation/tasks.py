"""Celery-optional background jobs for fiscalisation.

When Celery is installed the retry job is a real task that can be scheduled.
When it is not, the same function is still callable from a cron entry or a
management command, so a small school is not forced to run a broker.
"""

try:  # pragma: no cover - exercised only when Celery is installed
    from celery import shared_task
except ImportError:  # pragma: no cover - the common small-school case
    shared_task = None


def _retry(institution_id=None):
    from core.models import Institution

    from .services import retry_failed_submissions

    institution = None
    if institution_id is not None:
        institution = Institution.objects.filter(pk=institution_id).first()
    return retry_failed_submissions(institution)


if shared_task is not None:
    @shared_task(name="fiscalisation.retry_submissions")
    def retry_submissions(institution_id=None):
        return _retry(institution_id)
else:
    def retry_submissions(institution_id=None):
        return _retry(institution_id)
