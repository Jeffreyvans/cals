"""Background jobs: create the row, hand the work to Celery, report progress.

The rules are deliberately simple:

* a job always has a :class:`~core.models.BackgroundJob` row, so the page can
  show what is happening even when the worker is on another machine;
* when a broker is configured the task is queued and the request returns at
  once; without a broker the same function runs inline, so a single small
  deployment needs no worker at all;
* a job never changes another institution's data: the row is tenant-scoped and
  the worker reloads every object with ``all_objects`` only by the primary key
  stored on the job.
"""

import logging
import uuid

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone

from core import importer
from core.audit import log_action
from core.models import BackgroundJob, ImportBatch

logger = logging.getLogger("edusphere.jobs")


def jobs_available():
    """True when a real worker can pick a queued task up.

    ``CELERY_TASK_ALWAYS_EAGER`` is the default without a broker. In that case
    the view runs the function itself, which keeps development, the test suite
    and a tiny installation synchronous and predictable.
    """
    return bool(getattr(settings, "CELERY_BROKER_URL", "")) and not getattr(
        settings, "CELERY_TASK_ALWAYS_EAGER", False
    )


def create_job(kind, institution, user=None, *, label="", total=0, object_id=None):
    """Create the progress row before any work starts."""
    return BackgroundJob.objects.create(
        institution=institution,
        created_by=user if getattr(user, "is_authenticated", False) else None,
        kind=kind,
        label=(label or kind)[:200],
        total=max(0, int(total or 0)),
        object_id=object_id,
    )


def enqueue(task, job, *args):
    """Run ``task`` on the worker, falling back to this process.

    A broker that is down must not lose the user's import: the exception is
    logged and the same function runs inline. The request then waits, but the
    work is done and the job row records it.
    """
    if jobs_available():
        try:
            task.delay(job.pk, *args)
            return job
        except Exception:  # pragma: no cover - broker temporarily unavailable
            logger.exception("Could not queue job %s; running inline", job.pk)
    task(job.pk, *args)
    return job


def update_job(job, *, progress=None, processed=None, total=None, message=None):
    """Write progress without loading the row again."""
    fields = []
    if processed is not None:
        job.processed = max(0, int(processed))
        fields.append("processed")
    if total is not None:
        job.total = max(0, int(total))
        fields.append("total")
    if progress is not None:
        job.progress = max(0, min(100, int(progress)))
        fields.append("progress")
    elif job.total:
        job.progress = min(100, int(job.processed * 100 / job.total))
        fields.append("progress")
    if message is not None:
        job.message = str(message)[:255]
        fields.append("message")
    if fields:
        job.save(update_fields=fields)
    return job


def start_job(job, message=""):
    job.status = BackgroundJob.Status.RUNNING
    job.started = timezone.now()
    if message:
        job.message = str(message)[:255]
    job.save(update_fields=["status", "started", "message"])
    return job


def finish_job(job, result=None, message="Finished"):
    job.status = BackgroundJob.Status.DONE
    job.progress = 100
    job.message = str(message)[:255]
    job.result = result or {}
    job.finished = timezone.now()
    job.save(update_fields=["status", "progress", "message", "result", "finished"])
    return job


def fail_job(job, error):
    job.status = BackgroundJob.Status.FAILED
    job.error = str(error)[:4000]
    job.message = "The job could not finish."
    job.finished = timezone.now()
    job.save(update_fields=["status", "error", "message", "finished"])
    return job


def job_payload(job):
    """The small JSON object the progress page polls."""
    return {
        "id": job.pk,
        "status": job.status,
        "progress": job.percent,
        "processed": job.processed,
        "total": job.total,
        "message": job.message,
        "error": job.error,
        "url": (job.result or {}).get("url", ""),
        "download_url": (job.result or {}).get("download_url", ""),
    }


def store_job_file(job, filename, content, content_type):
    """Save a generated file and return the path stored on the job."""
    safe_name = (filename or "download").replace("/", "-").replace("\\", "-")
    path = default_storage.save(
        f"jobs/{job.institution_id}/{uuid.uuid4().hex}-{safe_name}",
        ContentFile(content),
    )
    job.result = {**(job.result or {}), "file": path, "filename": safe_name,
                  "content_type": content_type}
    job.save(update_fields=["result"])
    return path


# ---------- import ---------------------------------------------------------

def perform_import(batch, actor, job=None):
    """Validate and write one batch. Shared by the view and the worker."""
    from core import bulk_upload

    if batch.kind == bulk_upload.KIND:
        return bulk_upload.perform(batch, actor, job=job)
    if job is not None:
        update_job(job, progress=10, message="Checking the rows")
    results = importer.validate(
        batch.kind, batch.data_rows, batch.mapping, batch.institution)
    total = len(results)
    if job is not None:
        update_job(job, progress=35, total=total, message="Writing the rows")

    def on_progress(done, count):
        if job is not None:
            update_job(job, processed=done, total=count,
                       message=f"{done} of {count} rows written")

    imported, errors = importer.run_import(
        batch.kind, results, batch.institution, actor,
        access_mode=batch.access_mode, batch=batch, on_progress=on_progress,
    )
    batch.imported_count = imported
    batch.error_count = len(errors)
    batch.errors = errors
    batch.status = (
        ImportBatch.Status.IMPORTED if imported else ImportBatch.Status.FAILED
    )
    batch.finished = timezone.now()
    batch.save(update_fields=[
        "imported_count", "error_count", "errors", "status", "finished"])
    log_action(
        actor, "import.imported", batch,
        after={"kind": batch.kind, "imported": imported, "errors": len(errors),
               "access_mode": batch.access_mode},
        institution=batch.institution,
    )
    return imported, errors


# ---------- bulk email -----------------------------------------------------

def perform_newsletter(newsletter, actor, job=None):
    """Queue a newsletter, reporting progress for every recipient."""
    from operations.services import send_newsletter

    def on_progress(done, total):
        if job is not None:
            update_job(job, processed=done, total=total,
                       message=f"{done} of {total} recipients queued")

    return send_newsletter(newsletter, actor, on_progress=on_progress)


# ---------- PDF ------------------------------------------------------------

def perform_id_cards(job, card_ids):
    """Build a multi-card PDF and store it for the download link."""
    from admissions.documents import build_id_cards
    from admissions.models import StudentCard

    cards = list(
        StudentCard.all_objects.filter(
            pk__in=card_ids, institution_id=job.institution_id
        ).select_related(
            "student__institution", "student__programme", "student__user"
        )
    )
    if not cards:
        raise ValueError("No cards were selected.")
    if job is not None:
        update_job(job, progress=40, total=len(cards), message="Building the PDF")
    pdf = build_id_cards(cards)
    path = store_job_file(job, "student-cards.pdf", pdf, "application/pdf")
    if job is not None:
        update_job(job, processed=len(cards), progress=95, message="PDF ready")
    return {
        "url": f"/jobs/{job.pk}/",
        "download_url": f"/jobs/{job.pk}/download/",
        "file": path,
        "filename": "student-cards.pdf",
        "content_type": "application/pdf",
    }
