"""Configurable student and staff numbers.

An institution chooses the shape of its own numbers: a prefix, whether the
year is part of the number, how many digits the sequence is padded to, and the
next number to issue. The counter itself lives in a :class:`core.models.NumberSequence`
row and is advanced with an atomic update inside a lock, so two people
admitting at the same moment never share a number.

A number is only generated when one was not supplied. A number typed in by an
administrator (or imported from a spreadsheet) is always kept as it is.
"""

import time

from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F
from django.utils import timezone

from core.models import NumberSequence

KINDS = ("student", "staff")
DEFAULTS = {
    "student": {"prefix": "STU", "include_year": True, "padding": 4},
    "staff": {"prefix": "EMP", "include_year": True, "padding": 4},
}
MAX_PREFIX = 12
MIN_PADDING = 1
MAX_PADDING = 8
MAX_NEXT = 10_000_000


def get_format(institution, kind):
    """The stored number format for ``kind``, merged with the defaults."""
    stored = ((institution.settings or {}).get("numbering") or {}).get(kind) or {}
    base = dict(DEFAULTS.get(kind, DEFAULTS["student"]))
    for key in ("prefix", "include_year", "padding"):
        if key in stored:
            base[key] = stored[key]
    return base


def format_number(institution, kind, value, day=None):
    """Render one sequence value using the institution's format."""
    fmt = get_format(institution, kind)
    year = (day or timezone.localdate()).year
    parts = []
    if fmt["prefix"]:
        parts.append(str(fmt["prefix"]))
    if fmt["include_year"]:
        parts.append(str(year))
    parts.append(str(int(value)).zfill(int(fmt["padding"])))
    return "-".join(parts)


def peek_next(institution, kind):
    """The sequence value the next allocation would use."""
    row = NumberSequence.all_objects.filter(institution=institution, kind=kind).first()
    return (row.last_number if row else 0) + 1


def next_number(institution, kind, day=None):
    """Allocate the next number for ``kind`` and return it as a string.

    The counter is advanced with an atomic ``F()`` update inside a row lock, so
    simultaneous requests each get a distinct number. On SQLite (where
    ``select_for_update`` is a no-op) the unique constraint on the sequence row
    catches the rare double insert and the call retries, and a transient
    "database is locked" error is retried too.
    """
    for attempt in range(8):
        try:
            with transaction.atomic():
                sequence, _created = NumberSequence.all_objects.select_for_update(
                ).get_or_create(
                    institution=institution, kind=kind, defaults={"last_number": 0})
                NumberSequence.all_objects.filter(pk=sequence.pk).update(
                    last_number=F("last_number") + 1)
                sequence.refresh_from_db()
                return format_number(institution, kind, sequence.last_number, day)
        except IntegrityError:
            continue
        except OperationalError as exc:
            message = str(exc).lower()
            if "locked" not in message and "busy" not in message:
                raise
            time.sleep(0.02 * (attempt + 1))
            continue
    raise ValidationError(
        "Could not allocate a number right now. Please try again.")


def save_format(institution, kind, prefix, include_year, padding, next_value):
    """Validate and store a number format, and set the next number to issue."""
    if kind not in DEFAULTS:
        raise ValidationError("Unknown number format.")
    prefix = (prefix or "").strip().upper()
    if len(prefix) > MAX_PREFIX:
        raise ValidationError(f"The prefix may be at most {MAX_PREFIX} characters.")
    if prefix and not all(char.isalnum() or char in "-_" for char in prefix):
        raise ValidationError(
            "Use only letters, numbers, a dash or an underscore in the prefix.")
    try:
        padding = int(padding)
    except (TypeError, ValueError):
        raise ValidationError("The padding must be a whole number.") from None
    if not MIN_PADDING <= padding <= MAX_PADDING:
        raise ValidationError(
            f"The padding must be between {MIN_PADDING} and {MAX_PADDING}.")
    try:
        next_value = int(next_value)
    except (TypeError, ValueError):
        raise ValidationError("The next number must be a whole number.") from None
    if not 1 <= next_value <= MAX_NEXT:
        raise ValidationError(
            f"The next number must be between 1 and {MAX_NEXT:,}.")

    settings_row = dict(institution.settings or {})
    numbering = dict(settings_row.get("numbering") or {})
    numbering[kind] = {
        "prefix": prefix,
        "include_year": bool(include_year),
        "padding": padding,
    }
    settings_row["numbering"] = numbering
    institution.settings = settings_row
    institution.save(update_fields=["settings"])
    NumberSequence.all_objects.update_or_create(
        institution=institution, kind=kind,
        defaults={"last_number": next_value - 1})
    return get_format(institution, kind)


def formats_for(institution):
    """Both number formats with their next value and a preview, for a screen."""
    rows = {}
    for kind in KINDS:
        fmt = get_format(institution, kind)
        next_value = peek_next(institution, kind)
        rows[kind] = {
            "kind": kind,
            "prefix": fmt["prefix"],
            "include_year": fmt["include_year"],
            "padding": fmt["padding"],
            "next": next_value,
            "preview": format_number(institution, kind, next_value),
        }
    return rows
