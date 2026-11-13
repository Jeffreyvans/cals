"""Careers services: grading an attachment."""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from core.audit import log_action
from core.notifications import notify

from .models import AttachmentGrade


@transaction.atomic
def grade_placement(placement, mark, comments="", graded_by=None):
    """Record (or replace) the final grade for a placement and tell the student.

    The placement is marked completed once it has a grade, so the register and
    the student portal agree that the attachment has finished.
    """
    try:
        mark = Decimal(mark)
    except (InvalidOperation, TypeError) as exc:
        raise ValidationError(
            {"mark": "Enter a number between 0 and 100."}
        ) from exc
    grade, _created = AttachmentGrade.objects.update_or_create(
        placement=placement,
        defaults={"mark": mark, "comments": comments, "graded_by": graded_by},
    )
    grade.full_clean()
    placement.status = placement.Status.COMPLETED
    placement.save(update_fields=["status"])
    student_user = placement.student.user
    notify(
        student_user,
        "Attachment graded",
        f"Your attachment at {placement.employer} was graded {grade.letter}.",
        "/me/",
        "careers",
    )
    log_action(
        graded_by,
        "careers.attachment.graded",
        instance=grade,
        after={"mark": str(mark), "letter": grade.letter},
    )
    return grade
