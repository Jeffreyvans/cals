"""The bulk import screens.

The user uploads a file, says which column is which field, looks at the
problems and then writes the rows that are ready. The file is never written
before the user confirms, and every batch belongs to one institution.
"""

import csv

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from core import bulk_upload, importer, jobs, tasks
from core.access import user_has_code
from core.audit import log_action
from core.models import ImportBatch, Institution

PREVIEW_LIMIT = 25

IMPORT_ACCESS_MODE_CHOICES = (
    ("invite", "Send an invitation email"),
    ("temp_password", "Create a temporary password"),
    ("none", "No login yet"),
)
_IMPORT_ACCESS_MODE_DB = {
    "invite": ImportBatch.AccessMode.INVITATION,
    "temp_password": ImportBatch.AccessMode.TEMPORARY_PASSWORD,
    "none": ImportBatch.AccessMode.NO_LOGIN,
}
_IMPORT_ACCESS_MODE_UI = {
    ImportBatch.AccessMode.INVITATION: "invite",
    ImportBatch.AccessMode.TEMPORARY_PASSWORD: "temp_password",
    ImportBatch.AccessMode.NO_LOGIN: "none",
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
        max_bytes = importer.max_bytes()
        if upload.size > max_bytes:
            messages.error(
                request,
                f"That file is too big. The limit is {max_bytes // (1024 * 1024)} MB.")
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
        if batch.kind == bulk_upload.KIND:
            return redirect("bulk_upload_preview", pk=batch.pk)
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
    if batch.kind == bulk_upload.KIND:
        return redirect("bulk_upload_preview", pk=batch.pk)
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
    if batch.kind == bulk_upload.KIND:
        return redirect("bulk_upload_run", pk=batch.pk)
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
    if batch.kind == bulk_upload.KIND:
        return redirect("bulk_upload_result", pk=batch.pk)
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
    if batch.kind == bulk_upload.KIND:
        return redirect("bulk_upload_results", pk=batch.pk)
    credentials = batch.credentials or []
    from core.notifications import absolute_url

    institution = batch.institution
    login_link = (
        absolute_url(reverse("institution_login", args=[institution.slug]))
        if getattr(institution, "slug", "")
        else absolute_url(reverse("login"))
    )
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="import-credentials-{batch.pk}.csv"')
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow([
        "Name", "Username", "Email", "Access", "One-time password",
        "Student number", "Sign-in link", "Sign-in instructions",
    ])
    for row in credentials:
        username = row.get("username", "")
        instructions = (
            f"Sign in at {login_link} with your email or username {username}."
            if username else f"Sign in at {login_link}."
        )
        writer.writerow([
            row.get("name", ""),
            username,
            row.get("email", ""),
            row.get("access_mode", ""),
            row.get("password", ""),
            row.get("student_no", ""),
            login_link,
            instructions,
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
    if batch.kind == bulk_upload.KIND:
        return redirect("bulk_upload_undo", pk=batch.pk)
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
    if batch.kind == bulk_upload.KIND:
        return redirect("bulk_upload_report", pk=batch.pk)
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


# ---------- bulk upload of students ---------------------------------------

BULK_OPTION_FIELDS = {
    "create_missing_programmes": "create_programmes",
    "create_missing_courses": "create_courses",
    "create_missing_classes": "create_classes",
    "apply_registration_rules": "apply_rules",
    "remove_missing_enrolments": "remove_enrolments",
    "confirmed_remove": "confirm_remove",
    "update_year_grade": "update_year",
    "first_invoice": "first_invoice",
    "allow_manual_numbers": "manual_numbers",
    "send_invitations": "send_invitations",
}


def _bulk_options(request):
    """Read this batch's choices from the confirmation screen."""
    options = {key: request.POST.get(name) == "on" for key, name in BULK_OPTION_FIELDS.items()}
    options["term"] = request.POST.get("term", "")[:60]
    options["intake_year"] = request.POST.get("intake_year", "")[:20]
    if not options["remove_missing_enrolments"]:
        options["confirmed_remove"] = False
    return options


def _bulk_permission(request):
    if not user_has_code(request.user, "students.bulk_import"):
        raise PermissionDenied("Your role does not allow a bulk student upload.")


@login_required
def bulk_upload_start(request):
    """Upload a spreadsheet for the dedicated student upload screen."""
    _bulk_permission(request)
    inst = _institution(request)
    if request.method == "POST":
        upload = request.FILES.get("file")
        if upload is None:
            messages.error(request, "Choose a file to upload.")
            return redirect("bulk_upload_start")
        limit = int(getattr(settings, "BULK_UPLOAD_MAX_BYTES", importer.max_bytes()))
        if upload.size > limit:
            messages.error(
                request,
                f"That file is too big. The limit is {limit // (1024 * 1024)} MB.")
            return redirect("bulk_upload_start")
        try:
            rows = importer.read_rows(upload, upload.name)
        except importer.ImportError as exc:
            messages.error(request, str(exc))
            return redirect("bulk_upload_start")
        access_mode = request.POST.get("access_mode", "")
        if access_mode not in _IMPORT_ACCESS_MODE_DB:
            messages.error(request, "Choose how new students get access.")
            return redirect("bulk_upload_start")
        headers = list(rows[0].keys())
        batch = ImportBatch.objects.create(
            institution=inst,
            kind=bulk_upload.KIND,
            filename=upload.name[:255],
            rows=rows,
            mapping=importer.guess_mapping(headers, importer.FIELDS_BY_KIND[bulk_upload.KIND]),
            # The upload screen has no option controls. Keep the defaults until
            # the preview screen, where the user chooses the batch options.
            options=dict(bulk_upload.DEFAULT_OPTIONS),
            created_by=request.user,
            status=ImportBatch.Status.UPLOADED,
            access_mode=_IMPORT_ACCESS_MODE_DB[access_mode],
        )
        messages.success(request, f"{upload.name} uploaded. Check the columns.")
        return redirect("import_map", pk=batch.pk)
    return render(request, "core/bulk_upload_start.html", {
        "inst": inst,
        "access_modes": IMPORT_ACCESS_MODE_CHOICES,
        "default_options": bulk_upload.DEFAULT_OPTIONS,
        "max_rows": getattr(settings, "BULK_UPLOAD_MAX_ROWS", importer.max_rows()),
        "max_mb": int(getattr(settings, "BULK_UPLOAD_MAX_BYTES", importer.max_bytes()))
                  // (1024 * 1024),
    })


@login_required
def bulk_upload_template(request):
    """Download an Excel template with an example and an instructions sheet."""
    _bulk_permission(request)
    from openpyxl import Workbook
    from openpyxl.styles import Font

    fields = importer.FIELDS_BY_KIND[bulk_upload.KIND]
    example = {
        "registration_number": "STU-2026-0001",
        "first_name": "Tariro",
        "last_name": "Dube",
        "date_of_birth": "2008-03-04",
        "gender": "female",
        "email": "tariro.dube@example.com",
        "phone": "0771234567",
        "programme": "DIT",
        "year_or_grade": "1",
        "stream_or_cohort": "A",
        "intake_year": "2026",
        "guardian_name": "Mr Dube",
        "guardian_phone": "+263771234567",
        "courses": "IT101;IT102",
        "term": "Term 1",
        "status": "active",
        "username": "tdube",
    }
    book = Workbook()
    sheet = book.active
    sheet.title = "Students"
    sheet.append([field["label"] for field in fields])
    sheet.append([example.get(field["key"],
                              "Example course" if field["key"] == "course_1" else "")
                  for field in fields])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.freeze_panes = "A2"
    instructions = book.create_sheet("Instructions")
    lines = [
        ("How to use this template", ""),
        ("", ""),
        ("1. Put one student on each row of the Students sheet. Delete the example row.", ""),
        ("2. Leave a cell blank when you do not want to change that field.", ""),
        ("3. Registration number is optional. If it is blank, the institution's numbering "
         "format supplies one.", ""),
        ("4. To find an existing student, use a registration number, email or username. "
         "A surname, date of birth and programme match is only flagged for review.", ""),
        ("5. Put several courses in the 'Course codes or names' cell separated by semicolons, "
         "or use the Course 1 to Course 5 columns.", ""),
        ("6. Dates accept 2008-03-04, 04/03/2008 and 04 Mar 2008.", ""),
        ("7. Phone numbers accept 0771234567 and +263771234567.", ""),
        ("8. Check the preview before you confirm. Nothing is written until then.", ""),
        ("", ""),
        ("Column", "What it does"),
    ]
    for field in fields:
        lines.append((field["label"], field["help"] or "Optional."))
    for row in lines:
        instructions.append(list(row))
    instructions.column_dimensions["A"].width = 34
    instructions.column_dimensions["B"].width = 70
    for cell in instructions[1]:
        cell.font = Font(bold=True)
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = 'attachment; filename="edusphere-students-template.xlsx"'
    book.save(response)
    return response


@login_required
def bulk_upload_preview(request, pk):
    """Show every row's planned action before anything is saved."""
    batch = _batch(request, pk=pk)
    _may_import(request, batch.kind)
    if not batch.mapping:
        messages.error(request, "Map the columns first.")
        return redirect("import_map", pk=batch.pk)
    if request.method == "POST":
        batch.options = _bulk_options(request)
        batch.access_mode = _IMPORT_ACCESS_MODE_DB.get(
            request.POST.get("access_mode", ""), batch.access_mode)
        batch.save(update_fields=["options", "access_mode"])
        if request.POST.get("dry_run"):
            return redirect(f"{reverse('bulk_upload_preview', args=[batch.pk])}?dry_run=1")
        return redirect("bulk_upload_preview", pk=batch.pk)
    options = bulk_upload.options_for(batch)
    planner = bulk_upload.Planner(batch, options)
    results, summary = planner.build()
    kind_filter = request.GET.get("filter", "all")
    if kind_filter == "errors":
        shown = [row for row in results if row["errors"]]
    elif kind_filter == "warnings":
        shown = [row for row in results if row["warnings"]]
    else:
        shown = results
    limit = PREVIEW_LIMIT * 2
    options_form = {key: options[key] for key in BULK_OPTION_FIELDS}
    return render(request, "core/bulk_upload_preview.html", {
        "batch": batch,
        "results": shown[:limit],
        "shown_rows": min(limit, len(shown)),
        "filtered": len(shown),
        "filter": kind_filter,
        "summary": summary,
        "total": len(results),
        "dry_run": request.GET.get("dry_run") == "1",
        "options": options,
        "options_form": options_form,
        "access_modes": IMPORT_ACCESS_MODE_CHOICES,
        "current_access_mode": _IMPORT_ACCESS_MODE_UI.get(batch.access_mode, "invite"),
        "fields": importer.FIELDS_BY_KIND[bulk_upload.KIND],
    })


@login_required
def bulk_upload_run(request, pk):
    """Start the write as a background job, or confirm an inline development run."""
    batch = _batch(request, pk=pk)
    _may_import(request, batch.kind)
    if request.method != "POST":
        return redirect("bulk_upload_preview", pk=batch.pk)
    if not batch.mapping:
        messages.error(request, "Map the columns first.")
        return redirect("import_map", pk=batch.pk)
    batch.options = _bulk_options(request)
    batch.access_mode = _IMPORT_ACCESS_MODE_DB.get(
        request.POST.get("access_mode", ""), batch.access_mode)
    batch.status = ImportBatch.Status.UPLOADED
    batch.save(update_fields=["options", "access_mode", "status"])
    if batch.options["remove_missing_enrolments"] and not batch.options["confirmed_remove"]:
        messages.error(
            request,
            "Tick the confirmation box before removing enrolments that are not in the file.")
        return redirect("bulk_upload_preview", pk=batch.pk)
    job = jobs.create_job(
        "import", batch.institution, request.user,
        label=f"Bulk upload students from {batch.filename}",
        total=len(batch.data_rows), object_id=batch.pk,
    )
    bulk_upload.start_background(batch, request.user, job)
    messages.success(request, "The upload is running. This page shows its progress; "
                              "you can safely leave it.")
    return redirect("job_detail", pk=job.pk)


@login_required
def bulk_upload_result(request, pk):
    """The counts and links after a student upload."""
    batch = _batch(request, pk=pk)
    _may_import(request, batch.kind)
    return render(request, "core/bulk_upload_result.html", {
        "batch": batch,
        "summary": batch.summary or {},
        "errors": batch.errors or [],
        "warnings": batch.warnings or [],
        "has_credentials": bool(batch.credentials),
        "can_undo": bool(batch.undo_log) and not batch.undone_at,
    })


@login_required
def bulk_upload_results(request, pk):
    """Download registration numbers, usernames and one-time passwords once."""
    batch = _batch(request, pk=pk)
    _may_import(request, batch.kind)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="student-upload-results-{batch.pk}.csv"')
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(["Row", "Registration number", "Username", "Name", "Email",
                     "One-time password", "Access mode"])
    for row in batch.credentials or []:
        writer.writerow([
            row.get("row", ""), row.get("registration_number", row.get("student_no", "")),
            row.get("username", ""), row.get("name", ""), row.get("email", ""),
            row.get("password", ""), row.get("access_mode", ""),
        ])
    batch.credentials = []
    batch.credentials_downloaded = True
    batch.save(update_fields=["credentials", "credentials_downloaded"])
    log_action(
        request.user, "students.bulk_upload_results_downloaded", batch,
        request=request, after={"rows": len(batch.row_results or [])},
    )
    return response


@login_required
def bulk_upload_report(request, pk):
    """Download the row-level error or warning report."""
    batch = _batch(request, pk=pk)
    _may_import(request, batch.kind)
    which = request.GET.get("type", "errors")
    field = "warnings" if which == "warnings" else "errors"
    entries = batch.warnings if which == "warnings" else batch.errors
    if not entries and batch.mapping:
        # During preview nothing has been saved yet, so build the same plan the
        # preview screen shows and report its problems without writing anything.
        results, _summary = bulk_upload.Planner(batch).build()
        entries = [
            {"row": result["row"], "messages": result[field]}
            for result in results if result[field]
        ]
    headers = batch.headers
    data_rows = batch.data_rows
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="student-upload-{which}-{batch.pk}.csv"')
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(["Row"] + headers + ["Problem"])
    for entry in entries or []:
        number = entry.get("row")
        index = (number or 0) - 2
        values = data_rows[index] if 0 <= index < len(data_rows) else {}
        writer.writerow([number] + [values.get(header, "") for header in headers]
                        + ["; ".join(entry.get("messages", []))])
    return response


@login_required
def bulk_upload_undo(request, pk):
    """Revert unused created students and changed fields from this batch."""
    batch = _batch(request, pk=pk)
    _may_import(request, batch.kind)
    if request.method != "POST":
        return redirect("bulk_upload_result", pk=batch.pk)
    if batch.undone_at:
        messages.info(request, "This upload has already been undone.")
        return redirect("bulk_upload_result", pk=batch.pk)
    undone, skipped = bulk_upload.undo(batch, actor=request.user, request=request)
    messages.success(request, f"{undone} rows were undone.")
    if skipped:
        messages.warning(
            request,
            f"{len(skipped)} rows were kept because the records were used or have invoices.",
        )
    return redirect("bulk_upload_result", pk=batch.pk)
