"""Screens for the fiscalisation module."""

import qrcode
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from core.access import module_required, permission_required_code

from . import services
from .models import FiscalDay, FiscalDevice, FiscalSubmission


def _institution(request):
    return getattr(request, "institution", None) or getattr(
        request.user, "institution", None)


@login_required
@module_required("fiscalisation")
@permission_required_code("finance.manage_fiscalisation")
def dashboard(request):
    institution = _institution(request)
    day = services.current_fiscal_day(institution)
    submissions = FiscalSubmission.objects.select_related("fiscal_day")
    return render(request, "fiscalisation/dashboard.html", {
        "institution": institution,
        "device": FiscalDevice.all_objects.filter(institution=institution).first(),
        "fiscal_day": day,
        "submissions": submissions[:200],
        "gateway": services.get_gateway().name,
        "counts": {
            status: submissions.filter(status=status).count()
            for status, _label in FiscalSubmission.Status.choices
        },
    })


@login_required
@module_required("fiscalisation")
@permission_required_code("finance.manage_fiscalisation")
def open_day(request):
    if request.method == "POST":
        try:
            day = services.open_fiscal_day(_institution(request), request.user)
        except ValidationError as exc:
            messages.error(request, exc.messages[0] if exc.messages else str(exc))
        else:
            messages.success(request, f"Fiscal day {day.fiscal_day_no} is open.")
    return redirect("fiscalisation:dashboard")


@login_required
@module_required("fiscalisation")
@permission_required_code("finance.manage_fiscalisation")
def close_day(request):
    if request.method == "POST":
        try:
            day = services.close_fiscal_day(_institution(request), request.user)
        except ValidationError as exc:
            messages.error(request, exc.messages[0] if exc.messages else str(exc))
        else:
            messages.success(request, f"Fiscal day {day.fiscal_day_no} closed.")
    return redirect("fiscalisation:dashboard")


@login_required
@module_required("fiscalisation")
@permission_required_code("finance.manage_fiscalisation")
def retry(request, pk):
    submission = get_object_or_404(FiscalSubmission, pk=pk)
    instance = services.document_instance(submission)
    if instance is None:
        messages.error(request, "The original document is no longer available.")
    else:
        services.submit_document(instance, submission.document_type)
        submission.refresh_from_db()
        if submission.is_verified:
            messages.success(request, "Document accepted by ZIMRA.")
        else:
            messages.error(request, submission.last_error or "Submission failed.")
    return redirect("fiscalisation:dashboard")


def qr_image(request, pk):
    """Return the QR code ZIMRA gave this submission as a PNG."""
    submission = get_object_or_404(FiscalSubmission.all_objects, pk=pk)
    data = submission.qr_data or submission.verification_code or submission.document_number
    image = qrcode.make(data)
    buffer = HttpResponse(content_type="image/png")
    image.save(buffer, format="PNG")
    return buffer


def day_report(request):
    """A printable list of everything fiscalised on a day."""
    institution = _institution(request)
    raw = request.GET.get("day", "")
    day = None
    if raw:
        from datetime import date

        try:
            day = date.fromisoformat(raw)
        except ValueError:
            day = None
    if day is None:
        day = services.current_fiscal_day(institution)
        day = day.day if day else None
    fiscal_day = FiscalDay.all_objects.filter(
        institution=institution, day=day).first() if day else None
    submissions = FiscalSubmission.objects.filter(
        fiscal_day=fiscal_day) if fiscal_day else FiscalSubmission.objects.none()
    return render(request, "fiscalisation/day_report.html", {
        "fiscal_day": fiscal_day, "submissions": submissions})


def go_home(request):
    return HttpResponseRedirect(reverse("fiscalisation:dashboard"))
