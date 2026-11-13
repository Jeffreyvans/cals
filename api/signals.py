"""Queue webhook events for the things integrations care about.

Signals are used instead of editing every service so the domain apps stay
unaware of the API. Each receiver is a no-op when the institution has no
active endpoint, which is the normal case on a fresh install.

A receiver must never break the save that triggered it: any failure while
building or queueing the event is logged and swallowed.
"""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from academics.models import Student
from admissions.models import Application
from finance.models import Invoice, Payment

from .webhooks import dispatch_event

logger = logging.getLogger(__name__)


def _iso(value):
    if value is None:
        return None
    converter = getattr(value, "isoformat", None)
    return converter() if callable(converter) else str(value)


def _student_payload(student):
    return {
        "id": student.pk,
        "student_no": student.student_no,
        "name": student.name,
        "status": student.status,
        "programme": student.programme_id,
        "school_class": student.school_class_id,
    }


def _invoice_payload(invoice):
    return {
        "id": invoice.pk,
        "student": invoice.student_id,
        "student_no": invoice.student.student_no,
        "description": invoice.description,
        "amount": str(invoice.amount),
        "due_date": _iso(invoice.due_date),
        "term": invoice.term_id,
    }


def _payment_payload(payment):
    return {
        "id": payment.pk,
        "receipt_no": payment.receipt_no,
        "invoice": payment.invoice_id,
        "student": payment.invoice.student_id,
        "amount": str(payment.amount),
        "method": payment.method,
        "paid_on": _iso(payment.paid_on),
    }


def _application_payload(application):
    return {
        "id": application.pk,
        "reference": application.reference,
        "name": f"{application.first_name} {application.last_name}".strip(),
        "email": application.email,
        "status": application.status,
        "programme": application.programme_id,
        "submitted_at": _iso(application.submitted_at),
    }


def _queue(event, payload, institution):
    try:
        dispatch_event(event, payload, institution)
    except Exception:  # pragma: no cover - a webhook must never break a save
        logger.exception("Could not queue the %s webhook event", event)


@receiver(post_save, sender=Student, dispatch_uid="api.student_webhooks")
def student_saved(sender, instance, created, **kwargs):
    if created:
        _queue("student.created", _student_payload(instance), instance.institution)


@receiver(post_save, sender=Invoice, dispatch_uid="api.invoice_webhooks")
def invoice_saved(sender, instance, created, **kwargs):
    if created:
        _queue("invoice.created", _invoice_payload(instance), instance.institution)


@receiver(post_save, sender=Payment, dispatch_uid="api.payment_webhooks")
def payment_saved(sender, instance, created, **kwargs):
    if created:
        _queue("payment.received", _payment_payload(instance), instance.institution)


@receiver(post_save, sender=Application, dispatch_uid="api.application_webhooks")
def application_saved(sender, instance, created, **kwargs):
    if created:
        _queue(
            "application.submitted", _application_payload(instance), instance.institution
        )
