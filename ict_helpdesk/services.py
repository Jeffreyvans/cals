"""ICT helpdesk services: first responses, SLAs and account resets."""

import secrets
import string

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.audit import log_action

from .models import AccountResetRequest, Ticket

PASSWORD_ALPHABET = string.ascii_letters + string.digits


def generate_temporary_password(length=12):
    """A random password shown once to the person completing the reset."""
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


@transaction.atomic
def record_first_response(ticket, actor=None):
    """Start the SLA clock stop: the first time a technician replies."""
    if ticket.first_response_at is not None:
        return ticket
    ticket.first_response_at = timezone.now()
    if ticket.status == Ticket.Status.OPEN:
        ticket.status = Ticket.Status.ASSIGNED
    if actor is not None and getattr(actor, "is_authenticated", False):
        ticket.assigned_to = actor
    ticket.save(update_fields=["first_response_at", "status", "assigned_to"])
    log_action(actor, "ict.ticket.first_response", instance=ticket)
    return ticket


@transaction.atomic
def request_account_reset(user, requested_by=None, reason="", ticket=None):
    reset = AccountResetRequest.objects.create(
        user=user,
        requested_by=requested_by,
        reason=reason,
        ticket=ticket,
        institution=user.institution,
    )
    log_action(requested_by, "ict.account_reset.requested", instance=reset)
    return reset


@transaction.atomic
def complete_account_reset(reset, actor=None):
    """Set a new temporary password and close the request.

    The password is returned to the caller and is never written to the
    database, so it cannot leak from a later report.
    """
    if reset.status != AccountResetRequest.Status.PENDING:
        raise ValidationError("This reset has already been decided.")
    password = generate_temporary_password()
    user = reset.user
    user.set_password(password)
    user.save(update_fields=["password"])
    reset.status = AccountResetRequest.Status.COMPLETED
    reset.completed_at = timezone.now()
    reset.completed_by = actor if getattr(actor, "is_authenticated", False) else None
    reset.save(update_fields=["status", "completed_at", "completed_by"])
    log_action(actor, "ict.account_reset.completed", instance=reset)
    return reset, password


@transaction.atomic
def reject_account_reset(reset, actor=None):
    if reset.status != AccountResetRequest.Status.PENDING:
        raise ValidationError("This reset has already been decided.")
    reset.status = AccountResetRequest.Status.REJECTED
    reset.completed_at = timezone.now()
    reset.completed_by = actor if getattr(actor, "is_authenticated", False) else None
    reset.save(update_fields=["status", "completed_at", "completed_by"])
    log_action(actor, "ict.account_reset.rejected", instance=reset)
    return reset
