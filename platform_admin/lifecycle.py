"""Institution lifecycle rules in one place.

Every state change is a soft change: no institution row and no student record is
ever hard-deleted. That keeps the audit trail, invoices and data processing
records intact. The rules refuse transitions that would strand data:

* an institution must be archived before it can be deleted;
* the installation's default institution can never be deleted;
* a deleted institution must be restored before anything else;
* restoring an archived institution only makes it active again when it is not
  also suspended.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone


def _snapshot(institution):
    return {
        "is_active": institution.is_active,
        "suspended": institution.suspended_at is not None,
        "archived": institution.archived_at is not None,
        "deleted": institution.deleted_at is not None,
    }


@transaction.atomic
def change_lifecycle(institution, action, *, reason=""):
    """Apply one lifecycle action and return ``(before, after)``.

    Raises ``ValidationError`` with a message a platform administrator can act
    on. The caller logs the before/after values to the platform audit trail.
    """
    reason = (reason or "").strip()
    before = _snapshot(institution)
    now = timezone.now()

    if action not in {"suspend", "reinstate", "archive", "restore", "delete"}:
        raise ValidationError("Unknown institution action.")

    if action != "restore" and before["deleted"]:
        raise ValidationError("This institution was deleted. Restore it first.")

    if action == "suspend":
        institution.is_active = False
        institution.suspended_at = now
        institution.suspended_reason = reason

    elif action == "reinstate":
        if before["archived"]:
            raise ValidationError("Restore the archived institution before reinstating it.")
        institution.is_active = not before["deleted"]
        institution.suspended_at = None
        institution.suspended_reason = ""

    elif action == "archive":
        institution.is_active = False
        institution.archived_at = now
        institution.archived_reason = reason

    elif action == "restore":
        if not before["archived"]:
            raise ValidationError("This institution is not archived.")
        institution.archived_at = None
        institution.archived_reason = ""
        institution.deleted_at = None
        # A restored institution that was suspended stays suspended until the
        # platform deliberately reinstates it.
        institution.is_active = institution.suspended_at is None

    elif action == "delete":
        if institution.slug == "default":
            raise ValidationError("The default institution cannot be deleted.")
        if not before["archived"]:
            raise ValidationError(
                "Archive the institution first, then delete it. Nothing is ever "
                "hard-deleted, so this keeps the audit trail safe."
            )
        institution.is_active = False
        institution.deleted_at = now

    institution.save(
        update_fields=[
            "is_active",
            "suspended_at",
            "suspended_reason",
            "archived_at",
            "archived_reason",
            "deleted_at",
        ]
    )
    return before, _snapshot(institution)
