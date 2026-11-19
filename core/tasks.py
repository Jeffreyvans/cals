"""Celery tasks for the work a request should not wait for.

Every task takes a :class:`~core.models.BackgroundJob` primary key as its
first argument. The row already exists when the request returns, so the task
only has to load the object, do the work and record progress. The same
functions are called inline when no broker is configured.
"""

import logging

from celery import shared_task

from core import jobs
from core.models import BackgroundJob, ImportBatch

logger = logging.getLogger("edusphere.jobs")


def _load_job(job_id):
    job = BackgroundJob.all_objects.filter(pk=job_id).first()
    if job is None:
        logger.warning("Background job %s no longer exists", job_id)
        return None
    return job


@shared_task(name="core.run_import_job")
def run_import_job(job_id):
    """Validate and write a previously uploaded import batch."""
    job = _load_job(job_id)
    if job is None:
        return None
    batch = ImportBatch.all_objects.filter(
        pk=job.object_id, institution_id=job.institution_id
    ).first()
    if batch is None:
        return jobs.fail_job(job, "The uploaded file is no longer available.")
    try:
        jobs.start_job(job, "Checking the rows")
        imported, errors = jobs.perform_import(batch, job.created_by, job=job)
        return jobs.finish_job(
            job,
            {
                "url": f"/imports/{batch.pk}/result/",
                "imported": imported,
                "errors": len(errors),
            },
            f"{imported} rows imported",
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Import job %s failed", job.pk)
        return jobs.fail_job(job, exc)


@shared_task(name="core.send_bulk_upload_invites")
def send_bulk_upload_invites(job_id):
    """Deliver the queued invitations of one student upload in small chunks."""
    from core import bulk_upload

    job = _load_job(job_id)
    if job is None:
        return None
    batch = ImportBatch.all_objects.filter(
        pk=job.object_id, institution_id=job.institution_id).first()
    if batch is None:
        return jobs.fail_job(job, "The uploaded file is no longer available.")
    try:
        jobs.start_job(job, "Sending invitations")
        sent = bulk_upload.deliver_invitations(batch, job=job)
        return jobs.finish_job(
            job,
            {"url": f"/imports/{batch.pk}/result/", "sent": sent},
            f"{sent} invitations sent",
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Invitation job %s failed", job.pk)
        return jobs.fail_job(job, exc)


@shared_task(name="core.send_newsletter_job")
def send_newsletter_job(job_id):
    """Queue a newsletter for every recipient and show the progress."""
    from operations.models import Newsletter

    job = _load_job(job_id)
    if job is None:
        return None
    newsletter = Newsletter.all_objects.filter(
        pk=job.object_id, institution_id=job.institution_id
    ).first()
    if newsletter is None:
        return jobs.fail_job(job, "The newsletter no longer exists.")
    try:
        jobs.start_job(job, "Collecting recipients")
        count = jobs.perform_newsletter(newsletter, job.created_by, job=job)
        return jobs.finish_job(
            job,
            {"url": "/operations/marketing/newsletters/", "queued": count},
            f"Newsletter queued for {count} recipient(s)",
        )
    except Exception as exc:
        logger.exception("Newsletter job %s failed", job.pk)
        return jobs.fail_job(job, exc)


@shared_task(name="core.build_id_cards_job")
def build_id_cards_job(job_id, card_ids):
    """Build a multi-card PDF and store it for the download link."""
    job = _load_job(job_id)
    if job is None:
        return None
    try:
        jobs.start_job(job, "Building the PDF")
        result = jobs.perform_id_cards(job, card_ids)
        return jobs.finish_job(job, result, "PDF ready")
    except Exception as exc:
        logger.exception("ID card job %s failed", job.pk)
        return jobs.fail_job(job, exc)
