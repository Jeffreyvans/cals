"""The bulk import screens.

The user uploads a file, says which column is which field, looks at the
problems and then writes the rows that are ready. The file is never written
before the user confirms, and every batch belongs to one institution.
"""

import csv

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from core import importer, jobs, tasks
from core.access import user_has_code
from core.models import ImportBatch, Institution

PREVIEW_LIMIT = 25

IMPORT_ACCESS_MODE_CHOICES = (
    ("invite", "Send an invitation email"),
    ("temp_password", "Create a temporary password"),
)
_IMPORT_ACCESS_MODE_DB = {
    "invite": ImportBatch.AccessMode.INVITATION,
    "temp_password": ImportBatch.AccessMode.TEMPORARY_PASSWORD,
}
_IMPORT_ACCESS_MODE_UI = {
    ImportBatch.AccessMode.INVITATION: "invite",
    ImportBatch.AccessMode.TEMPORARY_PASSWORD: "temp_password",
}


def _institution(request):
    inst = getattr(request, "institution", None)
    if inst is None:
        inst = Institution.objects.filter(slug="default").first()
    return inst


def _may_import(request, kind):
    """Stop the request unless the user may import this kind of thing."""
    spec = importer.KINDS.get(kind)
    if spec is None:
        raise PermissionDenied("That kind of import is not known.")
    if not user_has_code(request.user, spec["permission"]):
        raise PermissionDenied("Your role does not allow this import.")


def _allowed_kinds(request):
    """The kinds of import this user may run."""
    return [
        (key, spec) for key, spec in importer.KINDS.items()
        if user_has_code(request.user, spec["permission"])
    ]


def _batch(request, pk):
    """The batch, if it belongs to this institution."""
    batch = get_object_or_404(ImportBatch, pk=pk)
    if batch.institution_id != getattr(_institution(request), "pk", None):
        raise PermissionDenied("That import belongs to another institution.")
    return batch


@login_required
def import_list(request):
    """Every import this institution has done."""
    inst = _institution(request)
    batches = ImportBatch.objects.filter(institution=inst).select_related("created_by")[:50]
    return render(request, "core/import_list.html", {
        "inst": inst,
        "batches": batches,
        "kinds": _allowed_kinds(request),
    })


@login_required
def import_start(request):
    """Choose what to import and upload the file."""
    inst = _institution(request)
    kinds = _allowed_kinds(request)
    if not kinds:
        raise PermissionDenied("Your role does not allow any import.")

    if request.method == "POST":
        kind = request.POST.get("kind", "")
        upload = request.FILES.get("file")
        if kind not in importer.KINDS:
            messages.error(request, "Choose what you are importing.")
            return redirect("import_start")
        _may_import(request, kind)
        if upload is None:
            messages.error(request, "Choose a file to upload.")
            return redirect("import_start")
        if upload.size > importer.MAX_BYTES:
            messages.error(
                request,
                f"That file is too big. The limit is {importer.MAX_BYTES // (1024 * 1024)} MB.")
            return redirect("import_start")
        try:
            rows = importer.read_rows(upload, upload.name)
        except importer.ImportError as exc:
            messages.error(request, str(exc))
            return redirect("import_start")
        headers = list(rows[0].keys())
        access_mode = request.POST.get("access_mode", "")
        if access_mode not in _IMPORT_ACCESS_MODE_DB:
            messages.error(request, "Choose how new users get access.")
            return redirect("import_start")
        batch = ImportBatch.objects.create(
            institution=inst,
            kind=kind,
            filename=upload.name[:255],
            rows=rows,
            mapping=importer.guess_mapping(headers, importer.FIELDS_BY_KIND[kind]),
            created_by=request.user,
            status=ImportBatch.Status.UPLOADED,
            access_mode=_IMPORT_ACCESS_MODE_DB[access_mode],
        )
        messages.success(request, f"{upload.name} uploaded. Check the columns.")
        return redirect("import_map", pk=batch.pk)

    return render(request, "core/import_start.html", {
        "inst": inst,
        "kinds": kinds,
    })


@login_required
def import_map(request, pk):
    """Say which column is which field."""
    batch = _batch(request, pk=pk)
    _may_import(request, batch.kind)
    fields = importer.FIELDS_BY_KIND[batch.kind]
    headers = batch.headers

    if request.method == "POST":
        mapping = {}
        for field in fields:
            column = request.POST.get(f"col_{field['key']}", "")
            if column and column in headers:
                mapping[field["key"]] = column
        if not mapping:
            messages.error(request, "Choose at least one column.")
            return redirect("import_map", pk=batch.pk)
        batch.mapping = mapping
        batch.status = ImportBatch.Status.MAPPED
        batch.save(update_fields=["mapping", "status"])
        return redirect("import_preview", pk=batch.pk)

    sample_rows = batch.data_rows[:1]
    field_rows = []
    for field in fields:
        column = batch.mapping.get(field["key"], "")
        sample = ""
        if column and sample_rows:
            sample = sample_rows[0].get(column, "")
        field_rows.append({**field, "column": column, "sample": sample})

    return render(request, "core/import_map.html", {
        "batch": batch,
        "field_rows": field_rows,
        "headers": headers,
        "rows": sample_rows,
    })


@login_required
def import_preview(request, pk):
    """Show what the file holds and what is wrong with it."""
    batch = _batch(request, pk=pk)
    _may_import(request, batch.kind)
    if not batch.mapping:
        messages.error(request, "Map the columns first.")
        return redirect("import_map", pk=batch.pk)
    if request.method == "POST":
        access_mode = request.POST.get("access_mode", "")
        if access_mode not in _IMPORT_ACCESS_MODE_DB:
            messages.error(request, "Choose how these people get access.")
            return redirect("import_preview", pk=batch.pk)
        batch.access_mode = _IMPORT_ACCESS_MODE_DB[access_mode]
        batch.save(update_fields=["access_mode"])
        return redirect("import_preview", pk=batch.pk)
    results = importer.validate(batch.kind, batch.data_rows, batch.mapping,
                                 batch.institution)
    ready = [r for r in results if not r["errors"]]
    broken = [r for r in results if r["errors"]]
    return render(request, "core/import_preview.html", {
        "batch": batch,
        "results": results[:PREVIEW_LIMIT],
        "ready": len(ready),
        "broken": len(broken),
        "total": len(results),
        "fields": importer.FIELDS_BY_KIND[batch.kind],
        "shown": min(PREVIEW_LIMIT, len(results)),
        "access_modes": IMPORT_ACCESS_MODE_CHOICES,
        "current_access_mode": _IMPORT_ACCESS_MODE_UI.get(batch.access_mode, "invite"),
        "needs_access_mode": batch.kind in ("students", "staff"),
    })


@login_required
def import_run(request, pk):
    """Write the rows that are ready."""
    batch = _batch(request, pk=pk)
    _may_import(request, batch.kind)
    if not batch.mapping:
        messages.error(request, "Map the columns first.")
        return redirect("import_map", pk=batch.pk)
    if jobs.jobs_available():
        job = jobs.create_job(
            "import", batch.institution, request.user,
            label=f"{batch.get_kind_display()} from {batch.filename}",
            total=len(batch.data_rows), object_id=batch.pk,
        )
        jobs.enqueue(tasks.run_import_job, job)
        messages.success(
            request,
            "The import is running in the background. This page shows its progress.")
        return redirect("job_detail", pk=job.pk)
    imported, errors = jobs.perform_import(batch, request.user)
    if imported:
        messages.success(request, f"{imported} rows imported.")
    if errors:
        messages.warning(request, f"{len(errors)} rows were left out. Download the report.")
    return redirect("import_result", pk=batch.pk)


@login_required
def import_result(request, pk):
    """What the import did."""
    batch = _batch(request, pk=pk)
    _may_import(request, batch.kind)
    return render(request, "core/import_result.html", {
        "batch": batch,
        "errors": batch.errors or [],
        "has_credentials": bool(batch.credentials),
        "can_undo": batch.kind in ("students", "staff")
        and not batch.undone_at
        and bool(batch.created_user_ids),
    })


@login_required
def import_credentials(request, pk):
    """Download usernames and one-time passwords, then forget the passwords."""
    batch = _batch(request, pk=pk)
    _may_import(request, batch.kind)
    credentials = batch.credentials or []
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="import-credentials-{batch.pk}.csv"')
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(["Name", "Username", "Email", "Access", "One-time password", "Student number"])
    for row in credentials:
        writer.writerow([
            row.get("name", ""),
            row.get("username", ""),
            row.get("email", ""),
            row.get("access_mode", ""),
            row.get("password", ""),
            row.get("student_no", ""),
        ])
    batch.credentials = []
    batch.credentials_downloaded = True
    batch.save(update_fields=["credentials", "credentials_downloaded"])
    return response


@login_required
def import_undo(request, pk):
    """Undo the accounts of a batch that nobody has signed in to yet."""
    batch = _batch(request, pk=pk)
    _may_import(request, batch.kind)
    if request.method != "POST":
        return redirect("import_result", pk=batch.pk)
    if batch.kind not in ("students", "staff"):
        messages.error(request, "Only student and staff imports can be undone.")
        return redirect("import_result", pk=batch.pk)
    if batch.undone_at:
        messages.info(request, "This import has already been undone.")
        return redirect("import_result", pk=batch.pk)
    undone, skipped = importer.undo_import(batch, actor=request.user, request=request)
    messages.success(request, f"{undone} accounts were deactivated.")
    if skipped:
        messages.warning(
            request,
            f"{len(skipped)} accounts were left active because they have already "
            "signed in or were already inactive.",
        )
    return redirect("import_result", pk=batch.pk)


@login_required
def import_template(request, kind):
    """Download an empty template with the column names this import expects."""
    _may_import(request, kind)
    fields = importer.FIELDS_BY_KIND[kind]
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="edusphere-{kind}-template.csv"')
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow([field["label"] for field in fields])
    return response


@login_required
def import_errors(request, pk):
    """Download the report of the rows that were left out."""
    batch = _batch(request, pk=pk)
    _may_import(request, batch.kind)
    errors = batch.errors or []
    headers = batch.headers
    data_rows = batch.data_rows
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="import-errors-{batch.pk}.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(["Row"] + headers + ["Problem"])
    for error in errors:
        number = error.get("row")
        index = (number or 0) - 2
        values = data_rows[index] if 0 <= index < len(data_rows) else {}
        writer.writerow([number] + [values.get(header, "") for header in headers]
                        + ["; ".join(error.get("messages", []))])
    return response
