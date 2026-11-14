"""Configurable student, staff and applicant numbers.

An institution chooses the shape of its own numbers: a fixed letter prefix,
whether the year is part of the number and whether it has two or four digits,
an optional programme or department code, how many digits the sequence is
padded to, one separator, and an optional final check letter. The counter
lives in a :class:`core.models.NumberSequence` row per institution, kind and
year bucket, and is advanced with an atomic update inside a lock, so two people
admitting at the same moment never share a number.

A number is only generated when one was not supplied. A number typed in by an
authorised administrator (or imported from a spreadsheet) is validated for
uniqueness, uppercased, logged and then kept exactly as it is.

The calculated check letter is deterministic and documented: each character
of the number before the check letter is given its position (1, 2, 3 ...) as a
weight and its value (a digit is 0-9, a letter is A=1 to Z=26). The weighted
sum modulo 26 is mapped to A-Z, with 0 mapping to A.
"""

import re
import time

from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F
from django.utils import timezone

from core.models import NumberFormat, NumberFormatChange, NumberSequence

KINDS = tuple(NumberFormat.Kind.values)
MAX_PREFIX = 12
MIN_PADDING = 1
MAX_PADDING = 8
MAX_NEXT = 10_000_000
SEPARATORS = ("", "-", "/")
CHECK_MODES = tuple(NumberFormat.CheckMode.values)
YEAR_DIGITS = (2, 4)

# The defaults keep the numbers an existing installation already produces.
DEFAULTS = {
    "student": {
        "prefix": "STU", "include_year": True, "year_digits": 4,
        "include_programme": False, "include_department": False,
        "padding": 4, "separator": "-", "check_mode": "none",
        "check_letter": "", "reset_yearly": True, "start_number": 1,
    },
    "staff": {
        "prefix": "EMP", "include_year": True, "year_digits": 4,
        "include_programme": False, "include_department": False,
        "padding": 4, "separator": "-", "check_mode": "none",
        "check_letter": "", "reset_yearly": True, "start_number": 1,
    },
    "applicant": {
        "prefix": "APP", "include_year": True, "year_digits": 4,
        "include_programme": False, "include_department": False,
        "padding": 4, "separator": "-", "check_mode": "none",
        "check_letter": "", "reset_yearly": True, "start_number": 1,
    },
}
FIELDS = tuple(DEFAULTS["student"])
MAX_LENGTHS = {"student": 20, "staff": 30, "applicant": 24}


def _validate_kind(kind):
    if kind not in KINDS:
        raise ValidationError("Unknown number type.")


def _as_bool(value, default=False):
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "on", "yes"}
    if value is None:
        return default
    return bool(value)


def get_format(institution, kind):
    """The stored number format for ``kind``, merged with the defaults."""
    _validate_kind(kind)
    base = dict(DEFAULTS[kind])
    stored = ((institution.settings or {}).get("numbering") or {}).get(kind) or {}
    for key in FIELDS:
        if key in stored:
            base[key] = stored[key]
    row = NumberFormat.all_objects.filter(
        institution=institution, kind=kind).first()
    if row is not None:
        for key in FIELDS:
            base[key] = getattr(row, key)
    # Normalise values that may have come from an older row or from JSON.
    base["prefix"] = str(base.get("prefix") or "").upper()
    base["include_year"] = _as_bool(base.get("include_year"), True)
    try:
        base["year_digits"] = int(base.get("year_digits") or 4)
    except (TypeError, ValueError):
        base["year_digits"] = 4
    if base["year_digits"] not in YEAR_DIGITS:
        base["year_digits"] = 4
    try:
        base["padding"] = int(base.get("padding") or 4)
    except (TypeError, ValueError):
        base["padding"] = 4
    base["separator"] = str(base.get("separator") or "")
    if base["separator"] not in SEPARATORS:
        base["separator"] = "-"
    base["check_mode"] = str(base.get("check_mode") or "none")
    if base["check_mode"] not in CHECK_MODES:
        base["check_mode"] = "none"
    base["check_letter"] = str(base.get("check_letter") or "").upper()[:1]
    base["reset_yearly"] = _as_bool(base.get("reset_yearly"), True)
    for key in ("include_programme", "include_department"):
        base[key] = _as_bool(base.get(key), False)
    try:
        base["start_number"] = max(int(base.get("start_number") or 1), 1)
    except (TypeError, ValueError):
        base["start_number"] = 1
    return base


def calculate_check_letter(base):
    """The documented weighted mod-26 check letter for a number stem."""
    total = 0
    for position, char in enumerate(str(base).upper(), start=1):
        if char.isdigit():
            value = int(char)
        elif "A" <= char <= "Z":
            value = ord(char) - 64
        else:
            continue
        total += position * value
    return chr(65 + total % 26)


def _sequence_year(fmt, day=None):
    if not fmt["reset_yearly"]:
        return 0
    return (day or timezone.localdate()).year


def format_number(institution, kind, value, day=None, programme_code="",
                  department_code="", format_row=None):
    """Render one sequence value using the institution's format."""
    fmt = format_row or get_format(institution, kind)
    year = (day or timezone.localdate()).year
    parts = []
    if fmt["prefix"]:
        parts.append(str(fmt["prefix"]))
    if fmt["include_year"]:
        digits = int(fmt["year_digits"])
        parts.append(str(year)[-digits:].zfill(digits))
    if fmt.get("include_programme") and programme_code:
        parts.append(str(programme_code).upper())
    if fmt.get("include_department") and department_code:
        parts.append(str(department_code).upper())
    parts.append(str(int(value)).zfill(int(fmt["padding"])))

    if fmt["check_mode"] == "fixed" and fmt.get("check_letter"):
        parts.append(str(fmt["check_letter"]).upper())
    elif fmt["check_mode"] == "calculated":
        parts.append(calculate_check_letter("".join(parts)))
    return str(fmt["separator"]).join(parts)


def peek_next(institution, kind, day=None):
    """The sequence value the next allocation would use."""
    fmt = get_format(institution, kind)
    bucket = _sequence_year(fmt, day)
    row = NumberSequence.all_objects.filter(
        institution=institution, kind=kind, year=bucket).first()
    return (row.last_number if row else fmt["start_number"] - 1) + 1


def preview_numbers(institution, kind, count=3, day=None, programme_code="",
                    department_code=""):
    """The next ``count`` numbers without consuming them."""
    fmt = get_format(institution, kind)
    start = peek_next(institution, kind, day)
    return [
        format_number(institution, kind, start + offset, day,
                      programme_code=programme_code,
                      department_code=department_code, format_row=fmt)
        for offset in range(max(int(count), 0))
    ]


def _number_in_use(institution, kind, candidate, exclude_pk=None):
    """True when another record already uses this number, ignoring case."""
    from academics.models import Student
    from admissions.models import Application
    from core.models import StaffProfile

    lowered = str(candidate).strip().casefold()
    if kind == "student":
        rows = Student.all_objects.filter(institution=institution)
        if exclude_pk:
            rows = rows.exclude(pk=exclude_pk)
        return any(str(number).strip().casefold() == lowered
                   for number in rows.values_list("student_no", flat=True))
    if kind == "staff":
        rows = StaffProfile.all_objects.filter(institution=institution)
        if exclude_pk:
            rows = rows.exclude(pk=exclude_pk)
        return any(str(number).strip().casefold() == lowered
                   for number in rows.values_list("employee_no", flat=True))
    rows = Application.all_objects.filter(institution=institution)
    if exclude_pk:
        rows = rows.exclude(pk=exclude_pk)
    return any(str(number).strip().casefold() == lowered
               for number in rows.values_list("reference", flat=True))


def next_number(institution, kind, day=None, programme_code="",
                department_code="", actor=None, max_attempts=8):
    """Allocate the next number for ``kind`` and return it as a string.

    The counter is advanced with an atomic ``F()`` update inside a row lock, so
    simultaneous requests each get a distinct number. On SQLite (where
    ``select_for_update`` is a no-op) the unique constraint on the sequence row
    catches the rare double insert and the call retries, and a transient
    "database is locked" error is retried too.
    """
    _validate_kind(kind)
    for attempt in range(max_attempts):
        try:
            with transaction.atomic():
                fmt = get_format(institution, kind)
                bucket = _sequence_year(fmt, day)
                sequence, _created = NumberSequence.all_objects.select_for_update(
                ).get_or_create(
                    institution=institution, kind=kind, year=bucket,
                    defaults={"last_number": fmt["start_number"] - 1})
                NumberSequence.all_objects.filter(pk=sequence.pk).update(
                    last_number=F("last_number") + 1)
                sequence.refresh_from_db()
                candidate = format_number(
                    institution, kind, sequence.last_number, day,
                    programme_code=programme_code,
                    department_code=department_code, format_row=fmt)
                if _number_in_use(institution, kind, candidate):
                    # Another record already has this shape (for example after
                    # an import). Move on rather than issue a duplicate.
                    continue
                return candidate
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


def number_key(value):
    """A comparison key that ignores case, spaces and separators."""
    return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).casefold()


def matching_pks(queryset, field, query, limit=None):
    """Primary keys whose ``field`` matches the query ignoring case/separators.

    List screens can OR this into their existing search filter, so a number
    printed as ``STU/2026/0001`` is still found when it is typed as
    ``stu 2026 0001``.
    """
    key = number_key(query)
    if not key:
        return []
    found = []
    for pk, value in queryset.values_list("pk", field):
        if key in number_key(value):
            found.append(pk)
            if limit is not None and len(found) >= limit:
                break
    return found


def _model_for_kind(kind):
    from academics.models import Student
    from admissions.models import Application
    from core.models import StaffProfile

    return {
        "student": (Student, "student_no"),
        "staff": (StaffProfile, "employee_no"),
        "applicant": (Application, "reference"),
    }[kind]


def validate_manual_number(institution, kind, number, actor=None, record=None,
                           request=None, log=True):
    """Validate and normalise a number typed or imported by an administrator.

    Manual numbers are uppercased, limited to letters, digits and the common
    separators, checked for a case-insensitive duplicate inside the same
    institution, and written to the audit trail. The returned value is the one
    that should be stored.
    """
    _validate_kind(kind)
    value = " ".join(str(number or "").split()).strip().upper()
    if not value:
        raise ValidationError("Enter a number.")
    limit = MAX_LENGTHS[kind]
    if len(value) > limit:
        raise ValidationError(f"The number may be at most {limit} characters.")
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9 ./_-]*", value):
        raise ValidationError(
            "Use only letters, digits, spaces, a dash, slash, underscore or full stop.")
    exclude_pk = getattr(record, "pk", None)
    if _number_in_use(institution, kind, value, exclude_pk=exclude_pk):
        raise ValidationError(
            f"The number '{value}' is already used by another record at this institution.")
    if log:
        from core.audit import log_action

        log_action(
            actor, "numbering.manual_override",
            model_name=_model_for_kind(kind)[0].__name__,
            object_id=str(exclude_pk or ""),
            institution=institution,
            after={"kind": kind, "number": value},
            request=request)
    return value


def save_format(institution, kind, prefix="", include_year=True, padding=4,
                next_value=None, *, year_digits=None, separator=None,
                include_programme=None, include_department=None,
                check_mode=None, check_letter="", reset_yearly=None,
                start_number=None, actor=None, reason="", request=None):
    """Validate and store a number format, and set the next number to issue."""
    _validate_kind(kind)
    prefix = str(prefix or "").strip().upper()
    if len(prefix) > MAX_PREFIX:
        raise ValidationError(f"The prefix may be at most {MAX_PREFIX} characters.")
    if prefix and not re.fullmatch(r"[A-Z0-9]+", prefix):
        raise ValidationError("Use only letters and digits in the prefix.")
    try:
        padding = int(padding)
    except (TypeError, ValueError):
        raise ValidationError("The padding must be a whole number.") from None
    if not MIN_PADDING <= padding <= MAX_PADDING:
        raise ValidationError(
            f"The padding must be between {MIN_PADDING} and {MAX_PADDING}.")

    include_year = _as_bool(include_year)
    if year_digits is None:
        year_digits = 4 if include_year else 0
    try:
        year_digits = int(year_digits or 0)
    except (TypeError, ValueError):
        raise ValidationError("The year must have two or four digits.") from None
    if include_year and year_digits not in YEAR_DIGITS:
        raise ValidationError("Choose a two- or four-digit year.")
    if not include_year:
        year_digits = 0

    current = get_format(institution, kind)
    # The short form on the institution page does not show every option, so a
    # missing value keeps the stored one rather than quietly clearing it.
    separator = current["separator"] if separator is None else str(separator)
    if separator not in SEPARATORS:
        raise ValidationError("Choose no separator, a hyphen or a slash.")

    check_mode = check_mode or current["check_mode"]
    if check_mode not in CHECK_MODES:
        raise ValidationError("Choose a valid check-letter option.")
    check_letter = str(check_letter or "").strip().upper()
    if check_mode == "fixed":
        if not check_letter:
            check_letter = current["check_letter"]
        if not re.fullmatch(r"[A-Z]", check_letter):
            raise ValidationError("A fixed check letter must be one letter, A to Z.")
    elif check_mode == "calculated":
        check_letter = ""
    else:
        check_letter = ""

    if start_number is None:
        start_number = current["start_number"]
    try:
        start_number = int(start_number)
    except (TypeError, ValueError):
        raise ValidationError("The start number must be a whole number.") from None
    if not 1 <= start_number <= MAX_NEXT:
        raise ValidationError(
            f"The start number must be between 1 and {MAX_NEXT:,}.")

    if next_value is None:
        # Without an explicit next value, a new start number seeds the
        # sequence; otherwise the current position is kept.
        next_value = (start_number if start_number != current["start_number"]
                      else peek_next(institution, kind))
    try:
        next_value = int(next_value)
    except (TypeError, ValueError):
        raise ValidationError("The next number must be a whole number.") from None
    if not 1 <= next_value <= MAX_NEXT:
        raise ValidationError(
            f"The next number must be between 1 and {MAX_NEXT:,}.")

    before = get_format(institution, kind)
    reset_yearly = _as_bool(
        reset_yearly if reset_yearly is not None else before.get("reset_yearly", True))
    data = {
        "prefix": prefix,
        "include_year": bool(include_year),
        "year_digits": year_digits,
        "include_programme": (_as_bool(include_programme)
                              if include_programme is not None
                              else before.get("include_programme", False)),
        "include_department": (_as_bool(include_department)
                               if include_department is not None
                               else before.get("include_department", False)),
        "padding": padding,
        "separator": separator,
        "check_mode": check_mode,
        "check_letter": check_letter,
        "reset_yearly": bool(reset_yearly),
        "start_number": start_number,
    }

    row, _created = NumberFormat.all_objects.update_or_create(
        institution=institution, kind=kind,
        defaults={**data, "updated_by": actor})
    bucket = _sequence_year(data)
    NumberSequence.all_objects.update_or_create(
        institution=institution, kind=kind, year=bucket,
        defaults={"last_number": next_value - 1})

    settings_row = dict(institution.settings or {})
    numbering = dict(settings_row.get("numbering") or {})
    numbering[kind] = {
        "prefix": prefix,
        "include_year": bool(include_year),
        "padding": padding,
        **{key: data[key] for key in FIELDS
           if key not in {"prefix", "include_year", "padding"}},
    }
    settings_row["numbering"] = numbering
    institution.settings = settings_row
    institution.save(update_fields=["settings"])

    NumberFormatChange.all_objects.create(
        institution=institution, format=row, kind=kind, actor=actor,
        before=before, after=data, reason=reason)
    from core.audit import log_action

    log_action(actor, "institution.numbering_updated", institution=institution,
               after={"kind": kind, **data}, request=request)
    return get_format(institution, kind)


def formats_for(institution):
    """Every number format with its next value and previews, for a screen."""
    from academics.models import Programme
    from core.models import Department

    programme_code = (
        Programme.all_objects.filter(institution=institution)
        .exclude(code="").values_list("code", flat=True).first() or "DIT")
    department_code = (
        Department.all_objects.filter(institution=institution)
        .exclude(code="").values_list("code", flat=True).first() or "DEP")
    rows = {}
    for kind in KINDS:
        fmt = get_format(institution, kind)
        next_value = peek_next(institution, kind)
        rows[kind] = {
            "kind": kind,
            **fmt,
            "next": next_value,
            "example_programme": programme_code,
            "example_department": department_code,
            "preview": format_number(
                institution, kind, next_value, programme_code=programme_code,
                department_code=department_code, format_row=fmt),
            "preview_three": preview_numbers(
                institution, kind, count=3, programme_code=programme_code,
                department_code=department_code),
        }
    return rows


def manual_override(institution, kind, current_number, new_number, actor=None,
                    request=None):
    """Give one existing record a different number, with validation and audit."""
    _validate_kind(kind)
    model, field = _model_for_kind(kind)
    record = None
    for candidate in model.all_objects.filter(institution=institution):
        if number_key(getattr(candidate, field)) == number_key(current_number):
            record = candidate
            break
    if record is None:
        raise ValidationError("No record at this institution uses that number.")
    value = validate_manual_number(
        institution, kind, new_number, actor=actor, record=record,
        request=request, log=False)
    old = getattr(record, field)
    setattr(record, field, value)
    record.save(update_fields=[field])
    from core.audit import log_action

    log_action(actor, "numbering.manual_override", instance=record,
               before={field: old},
               after={field: value, "number": value, "kind": kind},
               request=request)
    return record
