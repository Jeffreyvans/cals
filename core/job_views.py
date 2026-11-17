"""Progress and download screens for background jobs."""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.files.storage import default_storage
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, render

from core import jobs
from core.access import user_has_code
from core.models import BackgroundJob


def _job_for(request, pk):
    """The job, if it belongs to this institution and this person may see it."""
    job = get_object_or_404(BackgroundJob, pk=pk)
    if job.created_by_id != request.user.pk and not (
        request.user.is_superuser or user_has_code(request.user, "admin.manage_users")
    ):
        raise PermissionDenied("That job belongs to another person.")
    return job


@login_required
def job_detail(request, pk):
    """A small page that polls the job and follows the result when it is done."""
    job = _job_for(request, pk)
    return render(request, "core/job_detail.html", {
        "job": job,
        "payload": jobs.job_payload(job),
    })


@login_required
def job_status(request, pk):
    """JSON progress for the polling page."""
    job = _job_for(request, pk)
    return JsonResponse(jobs.job_payload(job))


@login_required
def job_download(request, pk):
    """Download a file a background job generated (for example a card PDF)."""
    job = _job_for(request, pk)
    result = job.result or {}
    path = result.get("file")
    if not path or not default_storage.exists(path):
        raise Http404("That file is no longer available.")
    return FileResponse(
        default_storage.open(path, "rb"),
        as_attachment=True,
        filename=result.get("filename") or "download",
        content_type=result.get("content_type") or "application/octet-stream",
    )
