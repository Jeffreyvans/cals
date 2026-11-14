"""Fiscalisation services: open/close fiscal days and submit documents."""

import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.document_header import snapshot_header
from core.modules import is_enabled
from finance.models import DocumentType

from .gateways import FiscalGatewayError, get_gateway
from .models import FiscalDay, FiscalDevice, FiscalSubmission

logger = logging.getLogger(__name__)


def _user(user):
    return user if getattr(user, "is_authenticated", False) else None


def fiscalisation_on(institution):
    """True when the module is on for a VAT-registered institution."""
    if institution is None:
        return False
    if not getattr(settings, "FISCALISATION_ENABLED", True):
        return False
    if getattr(institution, "vat_status", "") != "registered":
        return False
    return is_enabled("fiscalisation", institution)


def _document_date(instance):
    for attr in ("paid_on", "created", "date"):
        value = getattr(instance, attr, None)
        if value is not None:
            return value.date() if hasattr(value, "date") else value
    return timezone.localdate()


def document_number(instance):
    for attr in ("number", "receipt_no", "note_no"):
        value = getattr(instance, attr, "")
        if value:
            return value
    return f"{instance.pk}"


def open_fiscal_day(institution, user=None, day=None):
    """Open (or return) the fiscal day for a date and allocate its number."""
    day = day or timezone.localdate()
    device = FiscalDevice.for_institution(institution)
    with transaction.atomic():
        fiscal_day, created = FiscalDay.all_objects.select_for_update(
        ).get_or_create(
            institution=institution, day=day,
            defaults={"fiscal_day_no": device.last_fiscal_day_no + 1,
                      "opened_by": _user(user)})
        if created:
            device.last_fiscal_day_no = fiscal_day.fiscal_day_no
            device.save(update_fields=["last_fiscal_day_no"])
    if fiscal_day.status == FiscalDay.Status.CLOSED:
        raise ValidationError("The fiscal day for that date is already closed.")
    return fiscal_day


def close_fiscal_day(institution, user=None, day=None):
    day = day or timezone.localdate()
    fiscal_day = FiscalDay.all_objects.filter(
        institution=institution, day=day).first()
    if fiscal_day is None:
        raise ValidationError("There is no open fiscal day to close.")
    if fiscal_day.status == FiscalDay.Status.CLOSED:
        return fiscal_day
    fiscal_day.status = FiscalDay.Status.CLOSED
    fiscal_day.closed_at = timezone.now()
    fiscal_day.closed_by = _user(user)
    fiscal_day.save(update_fields=["status", "closed_at", "closed_by"])
    return fiscal_day


def current_fiscal_day(institution, day=None):
    day = day or timezone.localdate()
    return FiscalDay.all_objects.filter(institution=institution, day=day).first()


def _build_payload(instance, document_type, receipt_counter, fiscal_day):
    return {
        "document_type": document_type,
        "document_number": document_number(instance),
        "receipt_counter": receipt_counter,
        "fiscal_day_no": getattr(fiscal_day, "fiscal_day_no", 0),
        "amount": str(getattr(instance, "amount", "")),
        "currency": getattr(instance, "currency", ""),
        "tax_amount": str(getattr(instance, "tax_amount", "")),
    }


def submission_for(instance, document_type):
    return FiscalSubmission.all_objects.filter(
        institution=instance.institution, document_type=document_type,
        document_id=instance.pk).first()


def submit_document(instance, document_type):
    """Submit one issued document to the FDMS, creating or updating its row."""
    institution = instance.institution
    if not fiscalisation_on(institution):
        return None
    fiscal_day = open_fiscal_day(institution, day=_document_date(instance))
    submission, created = FiscalSubmission.all_objects.get_or_create(
        institution=institution, document_type=document_type,
        document_id=instance.pk,
        defaults={"document_number": document_number(instance),
                  "fiscal_day": fiscal_day,
                  "layout": snapshot_header(institution)})
    if submission.status == FiscalSubmission.Status.VERIFIED:
        return submission
    if not submission.layout:
        submission.layout = snapshot_header(institution)
    if submission.fiscal_day_id is None:
        submission.fiscal_day = fiscal_day
    counter = submission.receipt_counter or (
        submission.fiscal_day.submissions.count() + 1)
    device = FiscalDevice.for_institution(institution)
    payload = _build_payload(instance, document_type, counter, fiscal_day)
    submission.attempts += 1
    try:
        result = get_gateway().submit(device, fiscal_day, payload)
    except FiscalGatewayError as exc:
        submission.status = FiscalSubmission.Status.FAILED
        submission.last_error = str(exc)
        submission.save()
        return submission
    submission.status = result.get("status", FiscalSubmission.Status.VERIFIED)
    submission.receipt_counter = result.get("receipt_counter", counter)
    submission.verification_code = result.get("verification_code", "")
    submission.qr_data = result.get("qr_data", "")
    submission.response = result.get("response", {})
    submission.last_error = ""
    submission.submitted_at = timezone.now()
    submission.save()
    return submission


def document_instance(submission):
    """Fetch the finance document a submission belongs to."""
    from finance.models import DebitNote, Invoice, Payment, Waiver

    model = {
        DocumentType.INVOICE: Invoice,
        DocumentType.RECEIPT: Payment,
        DocumentType.CREDIT_NOTE: Waiver,
        DocumentType.DEBIT_NOTE: DebitNote,
    }.get(submission.document_type)
    if model is None:
        return None
    return model.all_objects.filter(pk=submission.document_id).first()


def retry_failed_submissions(institution=None):
    """Try again for every submission that is not yet verified."""
    pending = FiscalSubmission.all_objects.filter(
        status__in=[FiscalSubmission.Status.PENDING,
                    FiscalSubmission.Status.FAILED])
    if institution is not None:
        pending = pending.filter(institution=institution)
    retried = 0
    for submission in list(pending):
        instance = document_instance(submission)
        if instance is None:
            continue
        submit_document(instance, submission.document_type)
        retried += 1
    return retried


def on_financial_document_issued(sender, instance, document_type=None, **kwargs):
    """Signal receiver: fiscalise a document once it has been issued.

    A failure here must never break the finance save that triggered it, so any
    error is logged and swallowed; the submission row records the failure and
    the retry job picks it up.
    """
    if document_type is None:
        return
    try:
        submit_document(instance, document_type)
    except Exception:  # pragma: no cover - defensive
        logger.exception("Fiscalisation failed for %s %s", document_type,
                         getattr(instance, "pk", None))
