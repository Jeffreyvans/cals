"""Bulk upload of students: match, preview, write and undo.

This is the student-specific layer on top of the shared bulk import. It plans
every row before anything is saved, so the preview can show exactly what will
happen. A plan only reads data; :func:`perform` re-checks it and then writes each
row in its own transaction. Everything is scoped to the institution that owns
the batch.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, models, transaction
from django.urls import reverse
from django.utils import timezone

from core import importer
from core.audit import log_action
from core.email_types import STUDENT_INVITATION
from core.notifications import (
    absolute_url,
    bulk_email_limit_reached,
    record_bulk_email,
    send_email,
)
from core.numbering import number_key
from core.tenancy import use_institution

KIND = "student_upload"
COURSE_SEPARATORS = re.compile(r"[;\n|]+")

DEFAULT_OPTIONS = {
    "create_missing_programmes": False,
    "create_missing_courses": False,
    "create_missing_classes": False,
    "apply_registration_rules": True,
    "remove_missing_enrolments": False,
    "confirmed_remove": False,
    "update_year_grade": False,
    "first_invoice": False,
    "allow_manual_numbers": True,
    "send_invitations": True,
    "term": "",
    "intake_year": "",
}


def options_for(batch):
    """Batch options merged with safe defaults."""
    return {**DEFAULT_OPTIONS, **(batch.options or {})}


def _key(value):
    """A case-insensitive comparison key for names, codes and identifiers."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())).strip()


def _code_key(value):
    """Codes ignore case, spaces, hyphens and slashes when matched."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _programme_key(ref):
    """One canonical programme key, used for both stored and planned students."""
    if ref is None:
        return ""
    return _code_key(getattr(ref, "code", "")) or _key(getattr(ref, "name", ""))


def _code_from_name(name, taken, prefix="X"):
    base = re.sub(r"[^A-Z0-9]", "", str(name or "").upper())[:14] or prefix
    candidate = base
    suffix = 1
    while _code_key(candidate) in taken:
        candidate = f"{base}{suffix}"
        suffix += 1
    taken.add(_code_key(candidate))
    return candidate[:20]


def _display(value):
    if value is None or value == "":
        return ""
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _same_value(current, saved):
    """Compare a live model value with one restored from the JSON undo log."""
    if isinstance(current, (date, Decimal)) or isinstance(saved, (date, Decimal)):
        return _display(current) == _display(saved)
    return current == saved


def _jsonable(value):
    """Make a plan safe to store in the batch's JSON result columns.

    The live plan keeps model instances so the writer can resolve them in one
    place. Only the saved report needs plain ids and scalar values.
    """
    if isinstance(value, models.Model):
        return {"model": value._meta.label, "id": value.pk}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    try:
        return json.loads(json.dumps(value))
    except (TypeError, ValueError):
        return str(value)


def valid_phone(value):
    """Accept local and international Zimbabwean phone numbers."""
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("00263"):
        digits = digits[5:]
    if digits.startswith("263"):
        digits = digits[3:]
    if digits.startswith("0"):
        digits = digits[1:]
    return bool(re.fullmatch(r"7\d{8}", digits) or re.fullmatch(r"[2-9]\d{7,8}", digits))


def parse_courses(values):
    """Courses from the semicolon cell and the extra one-per-column cells."""
    result = []
    columns = ["courses", *(f"course_{index}" for index in range(1, 6))]
    for column in columns:
        for part in COURSE_SEPARATORS.split(values.get(column, "") or ""):
            part = part.strip()
            if part and part.lower() not in {item.lower() for item in result}:
                result.append(part)
    return result


def check_row(values, errors, institution=None, require_names=True):
    """Check one row's individual columns. Cross-row checks live in Planner.

    ``require_names`` is false in the planner because an existing student can
    be matched by registration number, email or username and then updated from
    a row that leaves the name cells blank. New rows still need both names.
    """
    from academics.models import Student

    values = dict(values or {})
    if require_names and not str(values.get("first_name", "")).strip():
        errors.append('"First name" is needed. Type the student\'s first name.')
    if require_names and not str(values.get("last_name", "")).strip():
        errors.append('"Surname" is needed. Type the student\'s surname.')
    if values.get("date_of_birth"):
        values["date_of_birth"] = importer.parse_date(
            values["date_of_birth"], "Date of birth", errors)
    if values.get("email"):
        email = values["email"].strip()
        try:
            validate_email(email)
        except ValidationError:
            errors.append(f'"Email" must be a real address (got {email!r}).')
        else:
            values["email"] = email.lower()
    if values.get("phone") and not valid_phone(values["phone"]):
        errors.append(
            f'"Phone" must be a Zimbabwean number such as 0771234567 or +263771234567 '
            f'(got {values["phone"]!r}).'
        )
    if values.get("guardian_phone") and not valid_phone(values["guardian_phone"]):
        errors.append(
            f'"Guardian phone" must be a Zimbabwean number such as 0771234567 '
            f'(got {values["guardian_phone"]!r}).'
        )
    if values.get("gender"):
        values["gender"] = importer.parse_choice(
            values["gender"], "Gender",
            tuple(Student.Gender.values), errors, default=Student.Gender.UNSPECIFIED)
    if values.get("status"):
        values["status"] = importer.parse_choice(
            values["status"], "Status", Student.Status.values, errors,
            default=Student.Status.ACTIVE)
    year = str(values.get("year_or_grade", "")).strip()
    if year.isdigit() and not 1 <= int(year) <= 20:
        errors.append(
            f'"Year or grade" must be between 1 and 20 (got {year}). '
            "Use a name such as Form 2 if the school uses forms."
        )
    return values


def _fingerprint(values):
    """Identity of a source row, used only to recognise a previous upload."""
    parts = [
        _key(values.get("registration_number")),
        _key(values.get("first_name")),
        _key(values.get("last_name")),
        _display(values.get("date_of_birth")),
        _key(values.get("programme")),
        _key(values.get("email")),
    ]
    if not any(parts):
        return ""
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]


def _fuzzy_key(values, programme_key=""):
    surname = _key(values.get("last_name"))
    born = _display(values.get("date_of_birth"))
    if not surname or not born:
        return ""
    return hashlib.sha256(
        f"{surname}|{born}|{programme_key}".encode()).hexdigest()[:24]


class Planner:
    """Read-only plan for one batch. Nothing here writes to the database."""

    def __init__(self, batch, options=None):
        self.batch = batch
        self.institution = batch.institution
        self.options = options or options_for(batch)
        self._loaded = False
        self.planned_objects = {}
        self._programme_taken = set()
        self._course_taken = set()
        self._class_taken = set()

    def load(self):
        if self._loaded:
            return
        from academics.models import (
            AcademicYear,
            Cohort,
            Course,
            CourseOffering,
            Enrollment,
            GradeLevel,
            Programme,
            ProgrammeYear,
            SchoolClass,
            Stream,
            Student,
            Term,
        )
        from accounts.models import User
        from core.models import BulkUploadMatch

        inst = self.institution
        self.Student = Student
        self.User = User
        self.programmes_by_code = {}
        self.programmes_by_name = {}
        for obj in Programme.all_objects.filter(institution=inst):
            self.programmes_by_code[_code_key(obj.code)] = obj
            self.programmes_by_name[_key(obj.name)] = obj
        self.courses_by_code = {}
        self.courses_by_name = {}
        for obj in Course.all_objects.filter(institution=inst).select_related("programme"):
            self.courses_by_code[_code_key(obj.code)] = obj
            self.courses_by_name.setdefault(_key(obj.title), []).append(obj)
        self.classes_by_name = {}
        self.classes_by_code = {}
        for obj in SchoolClass.all_objects.filter(institution=inst):
            self.classes_by_name[_key(obj.name)] = obj
            if obj.grade_level_id:
                self.classes_by_code[_code_key(obj.grade_level.code)] = obj
        self.streams = {}
        for obj in Stream.all_objects.filter(institution=inst).select_related("school_class"):
            self.streams[(obj.school_class_id, _key(obj.name))] = obj
        self.years_by_name = {}
        for obj in AcademicYear.all_objects.filter(institution=inst):
            self.years_by_name[_key(obj.name)] = obj
        self.programme_years = {}
        for obj in ProgrammeYear.all_objects.filter(institution=inst).select_related(
            "programme", "academic_year"
        ):
            self.programme_years[(obj.programme_id, obj.number)] = obj
            self.programme_years[(obj.programme_id, _key(obj.academic_year.name))] = obj
        self.cohorts = {}
        for obj in Cohort.all_objects.filter(institution=inst):
            self.cohorts[(obj.programme_year_id, _key(obj.name))] = obj
        self.terms_by_name = {}
        self.current_term = None
        for obj in Term.all_objects.filter(institution=inst).select_related("academic_year"):
            self.terms_by_name[_key(obj.name)] = obj
            if obj.is_current:
                self.current_term = obj
        self.current_year = AcademicYear.all_objects.filter(
            institution=inst, is_current=True).first()
        self.grade_levels = {
            _key(obj.name): obj
            for obj in GradeLevel.all_objects.filter(institution=inst)
        }
        self.grade_levels.update({
            _code_key(obj.code): obj
            for obj in GradeLevel.all_objects.filter(institution=inst) if obj.code
        })

        self.students = list(
            Student.all_objects.filter(institution=inst).select_related(
                "user", "programme", "programme_year", "school_class", "stream", "cohort")
        )
        self.by_number = {}
        self.by_email = {}
        self.by_username = {}
        self.by_fuzzy = {}
        for student in self.students:
            if student.student_no:
                self.by_number[number_key(student.student_no)] = student
            if student.user.email:
                self.by_email[_key(student.user.email)] = student
            if student.user.username:
                self.by_username[_code_key(student.user.username)] = student
            programme_key = _programme_key(getattr(student, "programme", None))
            fuzzy = _fuzzy_key(
                {"last_name": student.user.last_name, "date_of_birth": student.date_of_birth},
                programme_key,
            )
            if fuzzy:
                self.by_fuzzy.setdefault(fuzzy, []).append(student)
                any_programme = _fuzzy_key(
                    {"last_name": student.user.last_name, "date_of_birth": student.date_of_birth})
                if any_programme:
                    self.by_fuzzy.setdefault(any_programme, []).append(student)
        self.matches = {
            row.match_key: row.student
            for row in BulkUploadMatch.all_objects.filter(institution=inst).select_related("student")
        }
        self.enrolments = {}
        self.enrolments_by_student_term = {}
        for obj in Enrollment.all_objects.filter(institution=inst):
            self.enrolments[(obj.student_id, obj.course_id, obj.term_id)] = obj
            self.enrolments_by_student_term.setdefault((obj.student_id, obj.term_id), []).append(obj)
        self.offerings = {
            (obj.course_id, obj.term_id): obj
            for obj in CourseOffering.all_objects.filter(institution=inst).select_related(
                "course", "term", "course__programme")
        }
        self.prerequisites = {}
        for obj in Course.all_objects.filter(institution=inst).prefetch_related("prerequisites"):
            self.prerequisites[obj.pk] = list(obj.prerequisites.all())
        self._loaded = True

    # -- planned objects ---------------------------------------------------
    def _plan(self, model, entry):
        entry = {"key": f"new:{len(self.planned_objects) + 1}", "model": model, **entry}
        self.planned_objects[entry["key"]] = entry
        return entry

    def _use(self, ref, rows_created):
        """Record a planned object (and its parents) as used by this row."""
        if isinstance(ref, dict):
            key = ref.get("key")
            if key in self.planned_objects:
                rows_created.add(key)
            parent = ref.get("programme_key") or ref.get("year_key") or ref.get("class_key")
            if parent:
                self._use(self.planned_objects.get(parent, parent), rows_created)

    @staticmethod
    def _programme_level(name):
        text = _key(name)
        if any(word in text for word in ("form", "grade", "class", "primary", "secondary")):
            return "secondary"
        return "certificate"

    def resolve_programme(self, name, rows_created):
        self.load()
        key = _code_key(name) or _key(name)
        if not key:
            return None, None
        found = self.programmes_by_code.get(_code_key(name)) or self.programmes_by_name.get(
            _key(name))
        if found is not None:
            return found, None
        if not self.options["create_missing_programmes"]:
            return None, None
        code = _code_from_name(name, self._programme_taken, "PROG")
        entry = self._plan("Programme", {
            "code": code,
            "name": str(name)[:150],
            "level": self._programme_level(name),
            "note": "Created by import, review needed",
        })
        self.programmes_by_code[_code_key(code)] = entry
        self.programmes_by_name[_key(name)] = entry
        self._programme_taken.update(self.programmes_by_code)
        self._use(entry, rows_created)
        return entry, entry

    def resolve_course(self, name, programme_ref, rows_created):
        self.load()
        key = _code_key(name) or _key(name)
        if not key:
            return None, None
        found = self.courses_by_code.get(_code_key(name))
        if found is None:
            candidates = [
                obj for obj in self.courses_by_name.get(_key(name), [])
                if not programme_ref or obj.programme_id == self._ref_id(programme_ref)
            ]
            found = candidates[0] if len(candidates) == 1 else (
                candidates[0] if candidates else None)
        if found is not None:
            return found, None
        if not self.options["create_missing_courses"]:
            return None, None
        if programme_ref is None:
            return None, None
        code = _code_from_name(name, self._course_taken, "COURSE")
        entry = self._plan("Course", {
            "code": code,
            "title": str(name)[:150],
            "credits": 3,
            "programme_key": programme_ref.get("key") if isinstance(programme_ref, dict) else "",
            "programme_id": self._ref_id(programme_ref),
            "note": "Created by import, review needed",
        })
        self.courses_by_code[_code_key(code)] = entry
        self.courses_by_name.setdefault(_key(name), []).append(entry)
        self._course_taken.update(self.courses_by_code)
        self._use(entry, rows_created)
        return entry, entry

    def resolve_class(self, name, rows_created):
        self.load()
        if not name:
            return None, None
        found = self.classes_by_name.get(_key(name)) or self.classes_by_code.get(_code_key(name))
        if found is not None:
            return found, None
        if not self.options["create_missing_classes"]:
            return None, None
        entry = self._plan("SchoolClass", {
            "name": str(name)[:60],
            "level": "secondary" if "form" in _key(name) else "primary",
            "note": "Created by import, review needed",
        })
        self.classes_by_name[_key(name)] = entry
        self._class_taken.add(_key(name))
        self._use(entry, rows_created)
        return entry, entry

    def resolve_stream(self, name, class_ref, rows_created):
        self.load()
        if not name or class_ref is None:
            return None, None
        class_id = self._ref_id(class_ref)
        found = self.streams.get((class_id, _key(name)))
        if found is not None:
            return found, None
        if not self.options["create_missing_classes"]:
            return None, None
        entry = self._plan("Stream", {
            "name": str(name)[:30],
            "class_key": class_ref.get("key") if isinstance(class_ref, dict) else "",
            "class_id": class_id,
            "note": "Created by import, review needed",
        })
        self.streams[(class_id, _key(name))] = entry
        self._use(entry, rows_created)
        return entry, entry

    def resolve_year(self, name, year_or_grade="", rows_created=None):
        self.load()
        rows_created = rows_created if rows_created is not None else set()
        raw = str(name or "").strip()
        if not raw:
            return None, None
        found = self.years_by_name.get(_key(raw))
        if found is not None:
            return found, None
        if not self.options["create_missing_classes"] or not re.fullmatch(r"\d{4}", raw):
            return None, None
        try:
            year = int(raw)
        except ValueError:  # pragma: no cover - guarded by the regex
            return None, None
        entry = self._plan("AcademicYear", {
            "name": raw,
            "start": f"{year}-01-01",
            "end": f"{year}-12-31",
            "note": "Created by import, review needed",
        })
        self.years_by_name[_key(raw)] = entry
        self._use(entry, rows_created)
        return entry, entry

    def resolve_programme_year(self, programme_ref, year_ref, year_or_grade, rows_created):
        self.load()
        if programme_ref is None or year_ref is None:
            return None, None
        programme_id = self._ref_id(programme_ref)
        year_id = self._ref_id(year_ref)
        number = int(year_or_grade) if str(year_or_grade).isdigit() else 1
        found = self.programme_years.get((programme_id, number)) or self.programme_years.get(
            (programme_id, _key(getattr(year_ref, "name", ""))))
        if found is not None:
            return found, None
        if not self.options["create_missing_programmes"]:
            return None, None
        entry = self._plan("ProgrammeYear", {
            "number": number,
            "programme_key": programme_ref.get("key") if isinstance(programme_ref, dict) else "",
            "programme_id": programme_id,
            "year_key": year_ref.get("key") if isinstance(year_ref, dict) else "",
            "year_id": year_id,
            "note": "Created by import, review needed",
        })
        self.programme_years[(programme_id, number)] = entry
        self._use(entry, rows_created)
        return entry, entry

    def resolve_term(self, name):
        self.load()
        raw = str(name or "").strip() or str(self.options.get("term") or "").strip()
        if raw:
            found = self.terms_by_name.get(_key(raw))
            if found is not None:
                return found
        return self.current_term

    @staticmethod
    def _ref_id(ref):
        return ref.pk if hasattr(ref, "pk") else None

    # -- the plan itself ---------------------------------------------------
    def build(self):
        self.load()
        fields = importer.FIELDS_BY_KIND[KIND]
        results = []
        seen_numbers = {}
        seen_emails = {}
        seen_usernames = {}
        seen_fingerprints = {}
        for row_number, raw_row in enumerate(self.batch.data_rows, start=2):
            results.append(self._plan_row(
                row_number, raw_row, fields, seen_numbers, seen_emails,
                seen_usernames, seen_fingerprints, results))
        summary = {
            "created": sum(r["action"] == "create" for r in results),
            "updated": sum(r["action"] == "update" for r in results),
            "unchanged": sum(r["action"] == "no_change" for r in results),
            "skipped": sum(r["action"] == "skip" for r in results),
            "errors": sum(bool(r["errors"]) for r in results),
            "warnings": sum(bool(r["warnings"]) for r in results),
            "programmes_created": sum(
                1 for r in results for item in r["created"] if item["model"] == "Programme"),
            "courses_created": sum(
                1 for r in results for item in r["created"] if item["model"] == "Course"),
            "classes_created": sum(
                1 for r in results for item in r["created"]
                if item["model"] in ("SchoolClass", "Stream", "AcademicYear")),
        }
        return results, summary

    def _plan_row(self, row_number, raw_row, fields, seen_numbers, seen_emails,
                  seen_usernames, seen_fingerprints, results):
        def flag_previous(previous_row, marker, message):
            """Mark the earlier row in a duplicate pair as an error too."""
            for prior in results:
                if prior["row"] == previous_row:
                    if not any(marker in item for item in prior["errors"]):
                        prior["errors"].append(message)
                        prior["action"] = "error"
                    break

        values = {}
        for field in fields:
            column = self.batch.mapping.get(field["key"])
            if column:
                text = importer._clean(raw_row.get(column, ""))
                if text:
                    values[field["key"]] = text
        errors = []
        values = check_row(values, errors, self.institution, require_names=False)
        errors = [f"Row {row_number}: {message}" for message in errors]
        warnings = []
        changes = []
        created_keys = set()
        enrolments = []
        updates = {"Student": {}, "User": {}}
        student_data = {}
        undo = {"type": "create"}

        registration = str(values.get("registration_number", "") or "").strip().upper()
        email = str(values.get("email", "") or "").strip().lower()
        username = str(values.get("username", "") or "").strip().lower()

        if registration:
            key = number_key(registration)
            previous = seen_numbers.get(key)
            if previous:
                advice = ("Give the second student a different number or leave it blank.")
                errors.append(
                    f"Row {row_number}, Registration number: '{registration}' is used twice "
                    f"in this file (also row {previous}). {advice}")
                flag_previous(
                    previous, "Registration number",
                    f"Row {previous}, Registration number: '{registration}' is used twice "
                    f"in this file (also row {row_number}). {advice}")
            else:
                seen_numbers[key] = row_number
        if email:
            previous = seen_emails.get(email)
            if previous:
                advice = "Use a different address for each student."
                errors.append(
                    f"Row {row_number}, Email: '{email}' is used twice in this file "
                    f"(also row {previous}). {advice}")
                flag_previous(
                    previous, "Email",
                    f"Row {previous}, Email: '{email}' is used twice in this file "
                    f"(also row {row_number}). {advice}")
            else:
                seen_emails[email] = row_number
        if username:
            previous = seen_usernames.get(username)
            if previous:
                advice = "Leave it blank to generate one."
                errors.append(
                    f"Row {row_number}, Username: '{username}' is used twice in this file "
                    f"(also row {previous}). {advice}")
                flag_previous(
                    previous, "Username",
                    f"Row {previous}, Username: '{username}' is used twice in this file "
                    f"(also row {row_number}). {advice}")
            else:
                seen_usernames[username] = row_number

        fingerprint = _fingerprint(values)
        if fingerprint and fingerprint in seen_fingerprints:
            previous = seen_fingerprints[fingerprint]
            advice = ("Remove the duplicate row or give each student a registration number "
                      "or email.")
            errors.append(
                f"Row {row_number}: this is the same student as row {previous}. {advice}")
            flag_previous(
                previous, "same student",
                f"Row {previous}: this is the same student as row {row_number}. {advice}")
        elif fingerprint:
            seen_fingerprints[fingerprint] = row_number

        result = {
            "row": row_number,
            "action": "error" if errors else "create",
            "student_id": None,
            "registration_number": registration,
            "username": username,
            "name": f"{values.get('first_name', '')} {values.get('last_name', '')}".strip(),
            "match": "",
            "changes": changes,
            "created": [],
            "enrolments": enrolments,
            "warnings": warnings,
            "errors": errors,
            "fuzzy": False,
            "fingerprint": fingerprint,
            "updates": updates,
            "undo": undo,
            "student_data": student_data,
        }
        if errors:
            return result

        programme_ref, _created = self.resolve_programme(
            values.get("programme", ""), created_keys)
        programme_key = _programme_key(programme_ref)
        fuzzy_key = _fuzzy_key(values, programme_key)

        student = None
        match = ""
        if registration:
            student = self.by_number.get(number_key(registration))
            match = "registration number"
        if student is None and email:
            student = self.by_email.get(_key(email))
            match = "email"
        if student is None and username:
            student = self.by_username.get(_code_key(username))
            match = "username"
        if student is None and fingerprint and fingerprint in self.matches:
            student = self.matches[fingerprint]
            match = "previous import"

        candidates = []
        if student is None and fuzzy_key:
            candidates = list(dict.fromkeys(
                self.by_fuzzy.get(fuzzy_key, [])
                + self.by_fuzzy.get(_fuzzy_key(values), [])))
        if student is None and candidates:
            if len(candidates) == 1:
                result["action"] = "skip"
                result["fuzzy"] = True
                result["match"] = "surname, date of birth and programme"
                result["student_id"] = candidates[0].pk
                result["registration_number"] = candidates[0].student_no
                result["warnings"].append(
                    f"Row {row_number}: this looks like "
                    f"{candidates[0].student_no} ({candidates[0].name}) from the surname, "
                    "date of birth and programme. It was flagged for review and nothing was "
                    "changed. Add a registration number, email or username to update it.")
                return result
            errors.append(
                f"Row {row_number}, Surname/date of birth: more than one student could match. "
                "Add a registration number, email or username so the right student is chosen.")
            result["action"] = "error"
            result["errors"] = errors
            return result

        raw_programme = str(values.get("programme", "")).strip()
        class_ref = None
        if raw_programme and (
            programme_ref is None
            or any(word in _key(raw_programme) for word in ("form", "grade", "class"))
        ):
            class_ref, _created = self.resolve_class(raw_programme, created_keys)
        stream_ref, _created = self.resolve_stream(
            values.get("stream_or_cohort", ""), class_ref, created_keys)
        year_ref, _created = self.resolve_year(
            values.get("intake_year", ""), values.get("year_or_grade", ""), created_keys)
        programme_year_ref, _created = self.resolve_programme_year(
            programme_ref, year_ref, values.get("year_or_grade", ""), created_keys)
        term = self.resolve_term(values.get("term", ""))
        cohort = None
        if stream_ref is None and values.get("stream_or_cohort") and programme_year_ref:
            cohort = self.cohorts.get(
                (self._ref_id(programme_year_ref), _key(values["stream_or_cohort"])))
            if cohort is None and self.options["create_missing_classes"]:
                cohort = self._plan("Cohort", {
                    "name": str(values["stream_or_cohort"])[:60],
                    "programme_year_key": (
                        programme_year_ref.get("key")
                        if isinstance(programme_year_ref, dict) else ""),
                    "programme_year_id": self._ref_id(programme_year_ref),
                    "note": "Created by import, review needed",
                })
                self.cohorts[(
                    self._ref_id(programme_year_ref), _key(values["stream_or_cohort"]))] = cohort
                self._use(cohort, created_keys)

        if student is None:
            if not str(values.get("first_name", "")).strip():
                errors.append(
                    f"Row {row_number}: \"First name\" is needed. "
                    "Type the student's first name.")
            if not str(values.get("last_name", "")).strip():
                errors.append(
                    f"Row {row_number}: \"Surname\" is needed. "
                    "Type the student's surname.")
            if programme_ref is None:
                errors.append(
                    f"Row {row_number}, Programme or class: no programme was found for "
                    f"'{values.get('programme', '')}'. Tick 'Create missing' or add it first.")
                result["action"] = "error"
                result["errors"] = errors
                return result
            student_no = ""
            if registration:
                if self.options["allow_manual_numbers"]:
                    if number_key(registration) in self.by_number:
                        errors.append(
                            f"Row {row_number}, Registration number: '{registration}' is "
                            "already used by another student. Leave it blank to generate one.")
                    else:
                        student_no = registration
                else:
                    warnings.append(
                        f"Row {row_number}, Registration number: '{registration}' was ignored; "
                        "this batch uses the institution's numbering format.")
            changes.extend([
                {"field": "First name", "from": "", "to": values.get("first_name", "")},
                {"field": "Surname", "from": "", "to": values.get("last_name", "")},
                {"field": "Programme", "from": "", "to": _display(
                    getattr(programme_ref, "name", programme_ref))},
            ])
            student_data = {
                "first_name": values.get("first_name", ""),
                "last_name": values.get("last_name", ""),
                "email": email,
                "phone": values.get("phone", ""),
                "username": username,
                "programme_ref": programme_ref,
                "school_class_ref": class_ref,
                "stream_ref": stream_ref,
                "programme_year_ref": programme_year_ref,
                "cohort": cohort,
                "year_or_grade": values.get("year_or_grade", "") or "1",
                "date_of_birth": values.get("date_of_birth"),
                "gender": values.get("gender", ""),
                "guardian_name": values.get("guardian_name", ""),
                "guardian_phone": values.get("guardian_phone", ""),
                "status": values.get("status") or self.Student.Status.ACTIVE,
                "student_no": student_no,
                "first_invoice": bool(self.options["first_invoice"]),
                "access_mode": self.batch.access_mode,
            }
            result["student_data"] = student_data
            result["action"] = "error" if errors else "create"
            result["match"] = "new student"
            result["registration_number"] = student_no or "(generated)"
        else:
            if registration and number_key(registration) != number_key(student.student_no):
                warnings.append(
                    f"Row {row_number}, Registration number: kept '{student.student_no}'. "
                    "An existing student's registration number cannot be changed by an import.")
            self._plan_update(
                result, student, values, programme_ref, class_ref, stream_ref,
                programme_year_ref, cohort, updates, changes, warnings, errors)
            result["student_id"] = student.pk
            result["registration_number"] = student.student_no
            result["username"] = username or student.user.username
            result["match"] = match
            result["undo"] = {"type": "update"}
            result["action"] = "error" if errors else (
                "update" if updates["Student"] or updates["User"] else "no_change")

        self._plan_enrolments(
            result, student, values, programme_ref, term, created_keys, enrolments, warnings,
            errors)
        if result["action"] == "no_change" and any(
            entry["action"] in ("add", "drop") for entry in result["enrolments"]
        ):
            # Adding or dropping a course is still a change, even when no
            # student or user field changed.
            result["action"] = "update"
        result["created"] = [
            self.planned_objects[key] for key in sorted(
                created_keys, key=lambda item: list(self.planned_objects).index(item))
        ]
        for item in result["created"]:
            changes.append({
                "field": f"{item['model']} to create",
                "from": "",
                "to": f"{item.get('name') or item.get('title') or item.get('code')} "
                      "(created by import, review needed)",
            })
        if errors:
            result["action"] = "error"
            result["errors"] = errors
        return result

    def _plan_update(self, result, student, values, programme_ref, class_ref, stream_ref,
                     programme_year_ref, cohort, updates, changes, warnings, errors):
        row = result["row"]
        field_map = {
            "first_name": ("User", "first_name", student.user.first_name, "First name"),
            "last_name": ("User", "last_name", student.user.last_name, "Surname"),
            "email": ("User", "email", student.user.email, "Email"),
            "phone": ("User", "phone", student.user.phone, "Phone"),
            "guardian_name": ("Student", "guardian_name", student.guardian_name, "Guardian name"),
            "guardian_phone": ("Student", "guardian_phone", student.guardian_phone,
                               "Guardian phone"),
            "gender": ("Student", "gender", student.gender, "Gender"),
            "status": ("Student", "status", student.status, "Status"),
            "date_of_birth": ("Student", "date_of_birth", student.date_of_birth,
                              "Date of birth"),
        }
        for key, (model, field, old, label) in field_map.items():
            if key not in values:
                continue
            new = values[key]
            if field == "date_of_birth" and old is not None:
                if str(old) == str(new):
                    continue
            elif str(old or "") == str(new or ""):
                continue
            if field == "email" and any(
                other.pk != student.pk and _key(other.user.email) == _key(new)
                for other in self.students
            ):
                errors.append(
                    f"Row {row}, Email: '{new}' already belongs to another student at this "
                    "institution. Use a different address.")
                continue
            updates[model][field] = new
            changes.append({"field": label, "from": _display(old), "to": _display(new)})

        year = str(values.get("year_or_grade", "")).strip()
        if year and year != str(student.year_or_grade):
            if self.options["update_year_grade"]:
                updates["Student"]["year_or_grade"] = year[:20]
                changes.append({
                    "field": "Year or grade", "from": student.year_or_grade, "to": year})
            else:
                warnings.append(
                    f"Row {row}, Year or grade: '{student.year_or_grade}' would become '{year}', "
                    "but 'Update year or grade' is not ticked. The stored year was kept.")

        fk_changes = [
            ("programme", programme_ref, student.programme_id, "Programme"),
            ("school_class", class_ref, student.school_class_id, "Class"),
            ("stream", stream_ref, student.stream_id, "Stream"),
            ("programme_year", programme_year_ref, student.programme_year_id, "Programme year"),
        ]
        for field, ref, old_id, label in fk_changes:
            if ref is None and field != "programme":
                continue
            new_id = self._ref_id(ref)
            if new_id is None or new_id == old_id:
                continue
            updates["Student"][f"{field}_id"] = new_id
            changes.append({
                "field": label,
                "from": self._object_label(field, old_id),
                "to": self._object_label(field, new_id) or _display(
                    getattr(ref, "name", getattr(ref, "title", ref))),
            })
        if cohort is not None and cohort.pk != student.cohort_id:
            updates["Student"]["cohort_id"] = cohort.pk
            changes.append({"field": "Cohort", "from": self._object_label("cohort", student.cohort_id),
                            "to": str(cohort)})
        username = str(values.get("username", "")).strip().lower()
        if username and username != student.user.username.lower():
            if any(other.pk != student.pk and _code_key(other.user.username) == _code_key(username)
                   for other in self.students):
                errors.append(
                    f"Row {row}, Username: '{username}' already belongs to another account. "
                    "Choose a different username or leave the cell blank.")
            else:
                updates["User"]["username"] = username
                changes.append({
                    "field": "Username", "from": student.user.username, "to": username})

    def _object_label(self, field, pk):
        if not pk:
            return ""
        for student in self.students:
            current = getattr(student, field + "_id", None)
            if current == pk:
                return str(getattr(student, field))
        return str(pk)

    def _plan_enrolments(self, result, student, values, programme_ref, term,
                         created_keys, enrolments, warnings, errors):
        row = result["row"]
        course_names = parse_courses(values)
        desired_ids = set()
        offered = []
        for name in course_names:
            course_ref, _was_created = self.resolve_course(
                name, programme_ref, created_keys)
            if course_ref is None:
                errors.append(
                    f"Row {row}, Courses: '{name}' is not a known course. Add it first or "
                    "tick 'Create missing courses'.")
                continue
            course_id = self._ref_id(course_ref)
            if course_id:
                desired_ids.add(course_id)
            offering = self.offerings.get((course_id, term.pk if term else None))
            entry = {
                "course": name,
                "course_ref": course_ref,
                "term_id": term.pk if term else None,
                "offering_id": offering.pk if offering else None,
                "action": "add",
                "issues": [],
            }
            if student is not None and (student.pk, course_id, term.pk if term else None) \
                    in self.enrolments:
                entry["action"] = "already"
                warnings.append(
                    f"Row {row}, Courses: {student.student_no} is already enrolled for "
                    f"{name}. The existing enrolment was left alone.")
            elif self.options["apply_registration_rules"]:
                issues = self._registration_issues(result, student, course_ref, offering, term)
                entry["issues"] = issues
                if issues:
                    entry["action"] = "blocked"
                    warnings.append(
                        f"Row {row}, Courses: {name} was not added: {' '.join(issues)}")
            elif offering is not None and offering.capacity and offering.registered_count >= \
                    offering.capacity:
                warnings.append(
                    f"Row {row}, Courses: {name} is full. It was added anyway because "
                    "'Apply registration rules' is not ticked.")
            offered.append(entry)
        result["enrolments"] = [
            {
                "course": entry["course"],
                "course_ref": entry["course_ref"],
                "term_id": entry["term_id"],
                "offering_id": entry["offering_id"],
                "action": entry["action"],
                "issues": entry["issues"],
            }
            for entry in offered
        ]
        if not (self.options["remove_missing_enrolments"] and student is not None and term):
            return
        if not self.options["confirmed_remove"]:
            warnings.append(
                f"Row {row}: enrolments were not removed because the removal was not confirmed.")
            return
        for existing in self.enrolments_by_student_term.get((student.pk, term.pk), []):
            if existing.course_id in desired_ids or existing.status == "dropped":
                continue
            if existing.mark is not None:
                warnings.append(
                    f"Row {row}, Courses: {existing.course.code} was not removed because it "
                    "already has a mark. Its result was kept.")
                continue
            result["enrolments"].append({
                "course": existing.course.code,
                "course_ref": existing.course,
                "term_id": term.pk,
                "offering_id": existing.offering_id,
                "action": "drop",
                "enrollment_id": existing.pk,
                "issues": [],
            })
            result["changes"].append({
                "field": f"Remove enrolment {existing.course.code}",
                "from": existing.status, "to": "dropped"})

    def _registration_issues(self, result, student, course_ref, offering, term):
        from academics.services import registration_issues

        if student is not None and offering is not None:
            return registration_issues(student, offering)
        issues = []
        if isinstance(course_ref, dict):
            programme_id = course_ref.get("programme_id")
        else:
            programme_id = getattr(course_ref, "programme_id", None)
        student_programme_id = (
            student.programme_id if student is not None
            else self._ref_id(result["student_data"].get("programme_ref")))
        if programme_id and student_programme_id and programme_id != student_programme_id:
            issues.append("This course belongs to another programme.")
        if term is not None and getattr(term, "is_closed", False):
            issues.append(f"{term} is closed.")
        if offering is not None and offering.capacity and offering.registered_count >= \
                offering.capacity:
            issues.append(f"{course_ref.code if hasattr(course_ref, 'code') else 'This course'} "
                          "has no places left.")
        if self.prerequisites.get(self._ref_id(course_ref)):
            issues.append(
                "This course has prerequisites that cannot be checked until the student exists.")
        return issues


# ---------- writing a planned row ----------------------------------------

def _get_or_create(manager, lookup, defaults):
    """Create, tolerating a concurrent insert of the same unique row."""
    try:
        return manager.get_or_create(defaults=defaults, **lookup)
    except IntegrityError:
        return manager.get(**lookup), False


def _create_missing(entries, institution, actor):
    """Turn the planned objects into real rows, once per batch."""
    from academics.models import (
        AcademicYear,
        Cohort,
        Course,
        Programme,
        ProgrammeYear,
        SchoolClass,
        Stream,
    )

    created = {}
    for entry in entries or []:
        model = entry["model"]
        key = entry["key"]
        if model == "Programme":
            obj, _was_created = _get_or_create(
                Programme.all_objects,
                {"institution": institution, "code": entry["code"]},
                {
                    "name": entry["name"],
                    "level": entry.get("level") or Programme.Level.CERT,
                    "duration_months": 12,
                    "tuition_fee": 0,
                },
            )
        elif model == "Course":
            programme = created.get(entry.get("programme_key")) or Programme.all_objects.filter(
                pk=entry.get("programme_id"), institution=institution).first()
            if programme is None:
                raise ValueError("The programme for a new course is missing.")
            obj, _was_created = _get_or_create(
                Course.all_objects,
                {"institution": institution, "code": entry["code"]},
                {"title": entry["title"], "programme": programme, "credits": entry.get("credits", 3)},
            )
        elif model == "SchoolClass":
            obj, _was_created = _get_or_create(
                SchoolClass.all_objects,
                {"institution": institution, "name": entry["name"]},
                {"level": entry.get("level") or SchoolClass.Level.SECONDARY},
            )
        elif model == "Stream":
            school_class = created.get(entry.get("class_key")) or SchoolClass.all_objects.filter(
                pk=entry.get("class_id"), institution=institution).first()
            if school_class is None:
                raise ValueError("The class for a new stream is missing.")
            obj, _was_created = _get_or_create(
                Stream.all_objects,
                {"school_class": school_class, "name": entry["name"]},
                {},
            )
        elif model == "AcademicYear":
            obj, _was_created = _get_or_create(
                AcademicYear.all_objects,
                {"institution": institution, "name": entry["name"]},
                {
                    "start_date": date.fromisoformat(entry["start"]),
                    "end_date": date.fromisoformat(entry["end"]),
                    "is_current": False,
                    "is_closed": False,
                },
            )
        elif model == "ProgrammeYear":
            programme = created.get(entry.get("programme_key")) or Programme.all_objects.filter(
                pk=entry.get("programme_id"), institution=institution).first()
            year = created.get(entry.get("year_key")) or AcademicYear.all_objects.filter(
                pk=entry.get("year_id"), institution=institution).first()
            if programme is None or year is None:
                raise ValueError("The programme or year for a new programme year is missing.")
            obj, _was_created = _get_or_create(
                ProgrammeYear.all_objects,
                {"programme": programme, "academic_year": year},
                {"number": entry.get("number") or 1, "capacity": 0, "credit_limit": 0},
            )
        elif model == "Cohort":
            programme_year = created.get(entry.get("programme_year_key")) or \
                ProgrammeYear.all_objects.filter(
                    pk=entry.get("programme_year_id"), institution=institution).first()
            if programme_year is None:
                raise ValueError("The programme year for a new cohort is missing.")
            obj, _was_created = _get_or_create(
                Cohort.all_objects,
                {"programme_year": programme_year, "name": entry["name"]},
                {},
            )
        else:  # pragma: no cover - only the models above are planned
            raise ValueError(f"Cannot create {model} from a spreadsheet.")
        created[key] = obj
    return created


def _resolve(ref, created):
    if isinstance(ref, dict):
        return created.get(ref["key"])
    return ref


def _credential_info(student, account):
    return {
        "user_id": account.user.pk,
        "username": account.user.username,
        "email": account.user.email,
        "name": account.user.get_full_name() or account.user.username,
        "password": getattr(account, "password", "") or "",
        "access_mode": account.access_mode,
        "student_no": student.student_no,
    }


def _write_enrolments(result, student, created, institution, actor, options, undo):
    from django.core.exceptions import ValidationError as DjangoValidationError

    from academics.models import Enrollment, Term
    from academics.services import register_student

    warnings = []
    for entry in result.get("enrolments", []):
        if entry["action"] == "drop":
            enrollment = Enrollment.all_objects.filter(
                pk=entry["enrollment_id"], institution=institution).first()
            if enrollment and enrollment.status != "dropped":
                before = enrollment.status
                Enrollment.all_objects.filter(pk=enrollment.pk).update(status="dropped")
                undo.setdefault("changes", []).append({
                    "model": "Enrollment", "pk": enrollment.pk,
                    "before": {"status": before}, "after": {"status": "dropped"},
                })
            continue
        if entry["action"] != "add":
            continue
        course = _resolve(entry.get("course_ref"), created)
        if course is None:
            warnings.append(f"Row {result['row']}: {entry['course']} was not enrolled.")
            continue
        term = Term.all_objects.filter(pk=entry.get("term_id"), institution=institution).first()
        offering = None
        if entry.get("offering_id"):
            from academics.models import CourseOffering

            offering = CourseOffering.all_objects.filter(
                pk=entry["offering_id"], institution=institution).first()
        if options["apply_registration_rules"] and offering is not None:
            try:
                register_student(student, offering, user=actor)
            except (DjangoValidationError, ValueError) as exc:
                warnings.append(
                    f"Row {result['row']}, Courses: {entry['course']} was not enrolled: {exc}")
                continue
        else:
            enrollment, _was_created = _get_or_create(
                Enrollment.all_objects,
                {"student": student, "course": course, "term": term},
                {"status": "registered", "offering": offering},
            )
            if _was_created:
                undo.setdefault("enrollments", []).append(enrollment.pk)
    return warnings


def write_row(result, batch, actor, options):
    """Write one planned row inside the caller's transaction."""
    from academics.models import Student
    from accounts.models import User
    from admissions.services import create_student_record
    from core.models import BulkUploadMatch

    institution = batch.institution
    created = _create_missing(result.get("created"), institution, actor)
    undo = dict(result.get("undo") or {})
    credential = None
    warnings = []
    if result["action"] == "create":
        data = result["student_data"]
        info = create_student_record(
            institution,
            first_name=data["first_name"],
            last_name=data["last_name"],
            email=data["email"],
            phone=data["phone"],
            username=data["username"],
            programme=_resolve(data["programme_ref"], created),
            school_class=_resolve(data["school_class_ref"], created),
            stream=_resolve(data["stream_ref"], created),
            programme_year=_resolve(data["programme_year_ref"], created),
            cohort=data.get("cohort") if not isinstance(data.get("cohort"), dict) else created.get(
                data["cohort"]["key"]),
            year_or_grade=data["year_or_grade"],
            date_of_birth=data["date_of_birth"],
            guardian_name=data["guardian_name"],
            guardian_phone=data["guardian_phone"],
            student_no=data["student_no"],
            access_mode=data["access_mode"],
            actor=actor,
            first_invoice=data["first_invoice"],
            send_email=False,
        )
        student = info["student"]
        if data.get("gender"):
            student.gender = data["gender"]
            student.save(update_fields=["gender"])
        undo["student_id"] = student.pk
        undo["user_id"] = info["account"].user.pk
        undo["invoice_id"] = info["invoice"].pk if info["invoice"] else None
        credential = _credential_info(student, info["account"])
        credential["registration_number"] = student.student_no
        result["registration_number"] = student.student_no
        result["username"] = credential["username"]
        if result.get("fingerprint"):
            try:
                BulkUploadMatch.all_objects.get_or_create(
                    institution=institution,
                    match_key=result["fingerprint"],
                    defaults={
                        "student": student, "batch": batch, "source_row": result["row"],
                    },
                )
            except IntegrityError:  # a concurrent upload already recorded this row
                pass
    else:
        student = Student.all_objects.select_for_update().get(
            pk=result["student_id"], institution=institution)
        changes = []
        for model_name, fields in (result.get("updates") or {}).items():
            if not fields:
                continue
            manager = Student.all_objects if model_name == "Student" else User.objects
            obj = manager.select_for_update().get(
                pk=student.pk if model_name == "Student" else student.user_id)
            if model_name == "User" and obj.institution_id != institution.pk:
                raise ValueError("That login belongs to another institution.")
            before = {}
            for field, value in fields.items():
                before[field] = getattr(obj, field)
                setattr(obj, field, value)
            obj.save(update_fields=list(fields))
            changes.append({
                "model": model_name, "pk": obj.pk, "before": before, "after": dict(fields),
            })
        undo["changes"] = changes
    warnings.extend(_write_enrolments(
        result, student, created, institution, actor, options, undo))
    result["warnings"].extend(warnings)
    return {"credential": credential, "undo": undo}


# ---------- running and reporting ----------------------------------------

def perform(batch, actor, job=None):
    """Re-check the plan and write it, one transaction per row."""
    from core import jobs
    from core.models import ImportBatch

    options = options_for(batch)
    planner = Planner(batch, options)
    results, summary = planner.build()
    if job is not None:
        jobs.update_job(job, progress=20, total=len(results), message="Checking the rows")
    errors = []
    warnings = []
    credentials = []
    undo_log = []
    written = 0
    with use_institution(batch.institution):
        for index, result in enumerate(results, start=1):
            if result["errors"]:
                errors.append({"row": result["row"], "messages": result["errors"]})
            if (not result["errors"] and result["action"] not in ("skip", "error", "no_change")):
                try:
                    with transaction.atomic():
                        info = write_row(result, batch, actor, options)
                        written += 1
                        if info["credential"]:
                            credentials.append(info["credential"])
                        undo_log.append(info["undo"])
                except Exception as exc:  # one bad row must not stop the rest
                    errors.append({
                        "row": result["row"],
                        "messages": [f"Row {result['row']} could not be written: {exc}"],
                    })
            # Record warnings once, after writing, so warnings added while
            # writing are included too. Skipped rows keep their review notes.
            if result["warnings"]:
                warnings.append({"row": result["row"], "messages": result["warnings"]})
            if job is not None:
                jobs.update_job(job, processed=index, total=len(results),
                                message=f"{index} of {len(results)} rows processed")
    batch.row_results = [_jsonable(result) for result in results]
    batch.created_objects = [
        _jsonable(item)
        for result in results for item in result.get("created", [])
    ]
    batch.undo_log = [_jsonable(item) for item in undo_log]
    batch.credentials = credentials
    batch.errors = errors
    batch.warnings = warnings
    summary.update({"written": written, "errors": len(errors), "warnings":
                    sum(len(item["messages"]) for item in warnings)})
    batch.summary = summary
    batch.imported_count = written
    batch.error_count = len(errors)
    batch.status = ImportBatch.Status.IMPORTED if written else ImportBatch.Status.FAILED
    batch.finished = timezone.now()
    batch.save(update_fields=[
        "row_results", "created_objects", "undo_log", "credentials", "errors",
        "warnings", "summary", "imported_count", "error_count", "status", "finished",
    ])
    log_action(
        actor, "students.bulk_imported", batch,
        institution=batch.institution,
        after={
            "filename": batch.filename,
            "created": summary.get("created", 0),
            "updated": summary.get("updated", 0),
            "unchanged": summary.get("unchanged", 0),
            "skipped": summary.get("skipped", 0),
            "errors": len(errors),
            "programmes_created": summary.get("programmes_created", 0),
            "courses_created": summary.get("courses_created", 0),
        },
    )
    queue_invitations(batch, actor)
    return written, errors


def queue_invitations(batch, actor):
    """Queue invitation emails through the central email function.

    Provisioning deliberately does not send here: the messages are queued with
    ``send=False`` so a background job can deliver them in rate-limited chunks.
    """
    from accounts.models import User
    from accounts.user_admin import create_invitation
    from core.models import ImportBatch

    if batch.access_mode != ImportBatch.AccessMode.INVITATION:
        return 0
    if not options_for(batch)["send_invitations"]:
        return 0
    queued = 0
    with use_institution(batch.institution):
        for row in batch.credentials or []:
            user = User.objects.filter(pk=row.get("user_id"), institution=batch.institution).first()
            if user is None or not user.email:
                continue
            invitation = create_invitation(user, actor=actor, send=False)
            link = absolute_url(reverse("accept_invitation", args=[invitation.token]))
            body = (
                f"Hello {user.get_full_name() or user.username},\n\n"
                f"An account has been created for you at {batch.institution.name}.\n"
                f"Set your password here: {link}\n"
                f"You can also sign in with your email or username {user.username}.\n"
                "This link can only be used once and expires in 72 hours.\n"
            )
            send_email(
                STUDENT_INVITATION,
                user.email,
                f"Your {batch.institution.name} account",
                body,
                institution=batch.institution,
                related_object=invitation,
                send=False,
            )
            queued += 1
    return queued


def deliver_invitations(batch, job=None):
    """Send one rate-limited chunk of queued invitations.

    A call sends at most ``BULK_UPLOAD_EMAIL_CHUNK`` messages. The background
    job (or a later retry) can be run again to send the remaining chunk, so a
    large upload cannot flood the institution's email allowance.
    """
    from core import jobs
    from core.notifications import send_pending

    chunk = max(1, int(getattr(settings, "BULK_UPLOAD_EMAIL_CHUNK", 50)))
    if bulk_email_limit_reached(batch.institution):
        if job is not None:
            jobs.update_job(job, message="Institution email limit reached; try again later")
        return 0
    sent = send_pending(limit=chunk)
    if sent:
        record_bulk_email(batch.institution, sent)
    if job is not None:
        jobs.update_job(job, processed=sent, message=f"{sent} invitations sent")
    return sent


def _thread_run(job_pk, batch_pk, actor_pk):
    """Run a job in a short daemon thread when no Celery broker is configured."""
    from django.db import connections

    from core import jobs
    from core.models import BackgroundJob, ImportBatch

    try:
        job = BackgroundJob.all_objects.filter(pk=job_pk).first()
        batch = ImportBatch.all_objects.filter(pk=batch_pk).first()
        if job is None or batch is None:
            return
        from accounts.models import User

        actor = User.objects.filter(pk=actor_pk).first()
        with use_institution(batch.institution):
            jobs.start_job(job, "Running without a worker")
            perform(batch, actor, job=job)
            deliver_invitations(batch, job=job)
            jobs.finish_job(
                job, {"url": f"/imports/{batch.pk}/result/"}, "Student upload finished")
    except Exception as exc:  # pragma: no cover - defensive in a worker thread
        job = BackgroundJob.all_objects.filter(pk=job_pk).first()
        if job is not None:
            jobs.fail_job(job, exc)
    finally:
        connections.close_all()


def start_background(batch, actor, job):
    """Queue the work, or start a short thread so the request returns at once."""
    from core import jobs, tasks

    if jobs.jobs_available():
        return jobs.enqueue(tasks.run_import_job, job)
    thread = threading.Thread(
        target=_thread_run, args=(job.pk, batch.pk, actor.pk),
        name=f"bulk-upload-{batch.pk}", daemon=True,
    )
    thread.start()
    return job


# ---------- undo ---------------------------------------------------------

def _undo_blocker(student, institution):
    """Why a created student must be kept, or an empty string if it is unused."""
    from academics.models import AssessmentMark, Attendance, Enrollment
    from finance.models import Invoice, Payment

    if student.user.last_login is not None:
        return "the account has been used"
    if not student.user.is_active:
        return "the account is already inactive"
    # Invoices are immutable financial documents. Deleting the student would
    # cascade to the invoice, so a student with one is never removed by undo.
    if Invoice.all_objects.filter(institution=institution, student=student).exists():
        return "an invoice was issued"
    if Payment.objects.filter(invoice__student=student).exists():
        return "a payment was recorded"
    if Enrollment.all_objects.filter(student=student, mark__isnull=False).exists():
        return "a mark was recorded"
    if AssessmentMark.all_objects.filter(enrollment__student=student).exists():
        return "an assessment mark was recorded"
    if Attendance.all_objects.filter(enrollment__student=student).exists():
        return "attendance was recorded"
    return ""


def undo(batch, actor=None, request=None):
    """Undo rows this import created or changed, but never touch used records."""
    from academics.models import Enrollment, Student
    from accounts.models import User
    from accounts.user_admin import deactivate_user
    from core.models import OutboundMessage

    undone = 0
    skipped = []
    with use_institution(batch.institution):
        for entry in batch.undo_log or []:
            try:
                with transaction.atomic():
                    if entry.get("type") == "create" and entry.get("student_id"):
                        student = Student.all_objects.select_for_update().filter(
                            pk=entry["student_id"], institution=batch.institution).first()
                        if student is None:
                            continue
                        blocker = _undo_blocker(student, batch.institution)
                        if blocker:
                            skipped.append(
                                f"{student.student_no or student.user.username}: {blocker}")
                            continue
                        user = User.objects.filter(
                            pk=entry.get("user_id") or student.user_id,
                            institution=batch.institution,
                        ).first()
                        # A queued invitation must not arrive after the undo.
                        OutboundMessage.all_objects.filter(
                            institution=batch.institution,
                            recipient__iexact=getattr(user, "email", "") or student.user.email,
                            status=OutboundMessage.Status.QUEUED,
                            email_type=STUDENT_INVITATION,
                        ).update(status=OutboundMessage.Status.CANCELLED, next_attempt_at=None)
                        student.delete()
                        if user is not None and user.is_active:
                            deactivate_user(
                                user, actor=actor, request=request,
                                reason=f"Undo of bulk upload {batch.pk}",
                            )
                        undone += 1
                    else:
                        changes = entry.get("changes") or []
                        if not changes:
                            skipped.append(str(entry.get("student_id") or "row"))
                            continue
                        for change in changes:
                            model = change.get("model")
                            manager = Student.all_objects if model == "Student" else (
                                User.objects if model == "User" else Enrollment.all_objects)
                            obj = manager.select_for_update().filter(
                                pk=change["pk"], institution=batch.institution).first()
                            if obj is None or any(
                                not _same_value(getattr(obj, field), value)
                                for field, value in (change.get("after") or {}).items()
                            ):
                                # Somebody has edited this record since the
                                # import. Leave it exactly as they left it.
                                raise ValueError("The record has changed since the import.")
                            for field, value in (change.get("before") or {}).items():
                                setattr(obj, field, value)
                            obj.save(update_fields=list(change["before"]))
                        undone += 1
            except Exception:  # a row that cannot be undone is simply skipped
                skipped.append(str(entry.get("student_id") or "row"))
    from django.utils import timezone

    batch.undone_at = timezone.now()
    batch.undone_count = undone
    batch.save(update_fields=["undone_at", "undone_count"])
    log_action(
        actor, "students.bulk_upload_undone", batch, request=request,
        after={"undone": undone, "skipped": len(skipped)},
    )
    return undone, skipped
