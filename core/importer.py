"""Bulk import: read a spreadsheet, check it, then write it.

An import always happens in the same four steps:

1. the file is uploaded and read into rows,
2. the user says which column is which field,
3. every row is checked and the problems are shown,
4. the good rows are written and the bad ones can be downloaded as a report.

Nothing is written before step four, so a file with mistakes never half
arrives in the database. Every row belongs to the institution that uploaded
the file, and the rows of another institution are never touched.
"""

import csv
import io
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction

MAX_ROWS = 5000
MAX_BYTES = 5 * 1024 * 1024

TEXT = "text"
NUMBER = "number"
DECIMAL = "decimal"
DATE = "date"
CHOICE = "choice"


class ImportError(Exception):
    """Raised when a file cannot be read at all."""


def _field(key, label, required=False, kind=TEXT, aliases=(), help_text="", choices=()):
    return {
        "key": key,
        "label": label,
        "required": required,
        "kind": kind,
        "aliases": tuple(aliases),
        "help": help_text,
        "choices": tuple(choices),
    }


STUDENT_FIELDS = [
    _field("student_no", "Student number", aliases=("student no", "student number", "admission number", "id")),
    _field("first_name", "First name", required=True, aliases=("first name", "given name", "firstname")),
    _field("last_name", "Last name", required=True, aliases=("last name", "surname", "family name")),
    _field("email", "Email", aliases=("email", "email address", "student email")),
    _field("phone", "Phone", aliases=("phone", "mobile", "student phone")),
    _field("username", "Login username", aliases=("username", "login", "user name")),
    _field("programme", "Programme or level", required=True,
           aliases=("programme", "level", "course of study", "program")),
    _field("school_class", "Class", aliases=("class", "form", "grade", "class name")),
    _field("stream", "Stream", aliases=("stream",)),
    _field("year_or_grade", "Year or grade", aliases=("year", "year/grade", "year or grade")),
    _field("date_of_birth", "Date of birth", kind=DATE, aliases=("dob", "date of birth", "birth date")),
    _field("guardian_name", "Guardian name", aliases=("guardian", "guardian name", "parent", "parent name")),
    _field("guardian_phone", "Guardian phone", aliases=("guardian phone", "parent phone", "contact", "phone")),
    _field("status", "Status", kind=CHOICE, aliases=("status", "student status"),
           choices=("active", "deferred", "graduated", "withdrawn")),
]

STAFF_FIELDS = [
    _field("username", "Login username", required=True, aliases=("username", "login", "user name")),
    _field("first_name", "First name", required=True, aliases=("first name", "given name", "firstname")),
    _field("last_name", "Last name", required=True, aliases=("last name", "surname", "family name")),
    _field("email", "Email", aliases=("email", "email address")),
    _field("phone", "Phone", aliases=("phone", "contact", "mobile")),
    _field("employee_no", "Employee number", aliases=("employee no", "employee number", "staff number", "staff no")),
    _field("job_title", "Job title", aliases=("job title", "title", "position")),
    _field("department", "Department", aliases=("department", "dept")),
    _field("date_joined", "Date joined", kind=DATE, aliases=("date joined", "joined", "start date")),
    _field("role", "Role", aliases=("role", "user role"),
           help_text="An institution role code or name."),
]

COURSE_FIELDS = [
    _field("code", "Course code", required=True, aliases=("code", "course code", "course code/name")),
    _field("title", "Course title", required=True, aliases=("title", "course title", "name", "course name")),
    _field("programme", "Programme code", required=True, aliases=("programme", "programme code", "level")),
    _field("credits", "Credits", kind=NUMBER, aliases=("credits", "credit")),
    _field("lecturer", "Lecturer username", aliases=("lecturer", "teacher", "lecturer username")),
]

SUBJECT_FIELDS = [
    _field("code", "Subject code", required=True, aliases=("code", "subject code")),
    _field("name", "Subject name", required=True, aliases=("name", "subject name", "title")),
    _field("level", "Level", kind=CHOICE, aliases=("level",),
           choices=("primary", "secondary", "college")),
    _field("lecturer", "Lecturer username", aliases=("lecturer", "teacher", "lecturer username")),
]

FEE_STRUCTURE_FIELDS = [
    _field("structure_code", "Fee structure code", required=True,
           aliases=("structure code", "fee structure code", "code")),
    _field("structure_name", "Fee structure name", required=True,
           aliases=("structure name", "fee structure name", "name")),
    _field("item_name", "Fee item", required=True,
           aliases=("item", "item name", "fee item", "description", "line")),
    _field("amount", "Amount", required=True, kind=DECIMAL,
           aliases=("amount", "price", "fee", "value")),
    _field("mandatory", "Mandatory", kind=CHOICE, aliases=("mandatory", "compulsory"),
           choices=("yes", "no")),
    _field("academic_year", "Academic year", aliases=("academic year", "year")),
]


KINDS = {
    "students": {
        "label": "Students",
        "permission": "students.create",
        "fields": STUDENT_FIELDS,
        "help": "One row per student. First name and last name are needed.",
    },
    "staff": {
        "label": "Staff",
        "permission": "admin.manage_users",
        "fields": STAFF_FIELDS,
        "help": "One row per staff member. A login is created and the person is invited or given a temporary password.",
    },
    "courses": {
        "label": "Courses",
        "permission": "academics.manage_courses",
        "fields": COURSE_FIELDS,
        "help": "One row per course. The programme must already exist.",
    },
    "subjects": {
        "label": "Subjects",
        "permission": "academics.manage_courses",
        "fields": SUBJECT_FIELDS,
        "help": "One row per subject.",
    },
    "fee_structures": {
        "label": "Fee structures",
        "permission": "finance.manage_fee_structures",
        "fields": FEE_STRUCTURE_FIELDS,
        "help": "One row per fee item. Rows with the same structure code belong to one structure.",
    },
}


def kind_fields(kind):
    """The fields of one import kind."""
    return KINDS[kind]["fields"]


FIELDS_BY_KIND = {key: spec["fields"] for key, spec in KINDS.items()}


def kind_label(kind):
    return KINDS[kind]["label"]


# ---------- reading the file ----------
def read_rows(uploaded_file, filename):
    """Read an uploaded file into a list of rows.

    The first row is the header. Every row is a dictionary of column name to
    value, so the rest of the import never has to think about columns again.
    """
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        raw = _read_xlsx(uploaded_file)
    elif name.endswith(".csv"):
        raw = _read_csv(uploaded_file)
    else:
        raise ImportError("Upload a CSV file or an Excel workbook (.xlsx).")
    if not raw:
        raise ImportError("The file is empty.")
    headers = []
    for cell in raw[0]:
        header = str(cell or "").strip()
        if header and header not in headers:
            headers.append(header)
    if not headers:
        raise ImportError("The first row of the file must hold the column names.")
    rows = []
    for raw_row in raw[1:]:
        if not any(str(cell or "").strip() for cell in raw_row):
            continue
        row = {}
        for index, header in enumerate(headers):
            cell = raw_row[index] if index < len(raw_row) else ""
            row[header] = "" if cell is None else str(cell).strip()
        rows.append(row)
        if len(rows) > MAX_ROWS:
            raise ImportError(f"The file has more than {MAX_ROWS} rows. Split it into smaller files.")
    return [{header: header for header in headers}] + rows


def _read_csv(uploaded_file):
    uploaded_file.seek(0)
    raw = uploaded_file.read()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8-sig", errors="replace")
    sample = raw[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(raw), dialect)
    return [list(row) for row in reader]


def _read_xlsx(uploaded_file):
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise ImportError("Excel files need the openpyxl library. Install it or upload a CSV.") from exc
    uploaded_file.seek(0)
    try:
        book = load_workbook(uploaded_file, read_only=True, data_only=True)
    except Exception as exc:
        raise ImportError("That Excel file could not be opened.") from exc
    sheet = book[book.sheetnames[0]]
    rows = []
    for row in sheet.iter_rows(values_only=True):
        rows.append(["" if cell is None else cell for cell in row])
    book.close()
    return rows


# ---------- mapping the columns ----------
def guess_mapping(headers, fields):
    """Guess which column belongs to which field."""
    mapping = {}
    taken = set()
    for field in fields:
        wanted = [field["label"].lower(), field["key"].replace("_", " ")] + list(field["aliases"])
        for header in headers:
            if header in taken:
                continue
            lowered = header.lower().strip()
            if lowered in wanted:
                mapping[field["key"]] = header
                taken.add(header)
                break
    return mapping


# ---------- reading the values ----------
def _clean(value):
    return (value or "").strip() if isinstance(value, str) else ("" if value is None else str(value).strip())


def parse_date(value, label, errors):
    """Accept 2026-01-13, 13/01/2026 and 13-01-2026."""
    text = _clean(value)
    if not text:
        return None
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    errors.append(f"{label} is not a date ({text}).")
    return None


def parse_number(value, label, errors, whole=True):
    text = _clean(value)
    if not text:
        return None
    try:
        if whole:
            return int(text)
        return Decimal(text)
    except (ValueError, InvalidOperation):
        errors.append(f"{label} is not a number ({text}).")
        return None


def parse_money(value, label, errors):
    text = _clean(value)
    if not text:
        return None
    text = re.sub(r"[^\d.,\-]", "", text)
    if re.fullmatch(r"-?\d{1,3}(,\d{3})+(\.\d+)?", text):
        text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return Decimal(text).quantize(Decimal("0.01"))
    except (ValueError, InvalidOperation):
        errors.append(f"{label} is not an amount ({value}).")
        return None


def parse_choice(value, label, choices, errors, default=""):
    text = _clean(value).lower()
    if not text:
        return default
    if text not in choices:
        errors.append(f"{label} must be one of: {', '.join(choices)}.")
        return ""
    return text


def parse_yes_no(value, label, errors, default=True):
    text = _clean(value).lower()
    if not text:
        return default
    if text in ("yes", "y", "true", "1", "required", "compulsory"):
        return True
    if text in ("no", "n", "false", "0", "optional"):
        return False
    errors.append(f"{label} must be yes or no.")
    return default


# ---------- checking the rows ----------
class Context:
    """Everything the checkers need to look up, fetched once per import."""

    def __init__(self, institution):
        self.institution = institution
        self.programmes = {}
        self.classes = {}
        self.streams = {}
        self.users = {}
        self.years = {}
        self.roles = {}
        self._loaded = False

    def load(self):
        if self._loaded:
            return
        from academics.models import AcademicYear, Programme, SchoolClass, Stream
        from core.models import Role

        for row in Programme.all_objects.filter(institution=self.institution):
            self.programmes[row.code.strip().lower()] = row
            self.programmes[row.name.strip().lower()] = row
        for row in SchoolClass.all_objects.filter(institution=self.institution):
            self.classes[row.name.strip().lower()] = row
        for row in Stream.all_objects.filter(institution=self.institution):
            self.streams[(row.school_class_id, row.name.strip().lower())] = row
        for row in AcademicYear.all_objects.filter(institution=self.institution):
            self.years[row.name.strip().lower()] = row
        for row in Role.all_objects.filter(institution=self.institution):
            self.roles[row.code.strip().lower()] = row
            self.roles[row.name.strip().lower()] = row
        self._loaded = True

    def programme(self, name):
        self.load()
        return self.programmes.get((name or "").strip().lower())

    def school_class(self, name):
        self.load()
        return self.classes.get((name or "").strip().lower())

    def stream(self, school_class, name):
        self.load()
        key = (school_class.pk if school_class else None, (name or "").strip().lower())
        return self.streams.get(key)

    def academic_year(self, name):
        self.load()
        return self.years.get((name or "").strip().lower())

    def role(self, name):
        """An institution role, looked up by code or by name."""
        self.load()
        return self.roles.get((name or "").strip().lower())

    def taken_username(self, username):
        from accounts.models import User

        return User.objects.filter(username__iexact=username).exists()

    def taken_email(self, email, exclude_pk=None):
        from accounts.models import User

        if not email:
            return False
        queryset = User.objects.filter(
            institution=self.institution, email__iexact=email, is_active=True
        )
        if exclude_pk is not None:
            queryset = queryset.exclude(pk=exclude_pk)
        return queryset.exists()


def validate(kind, rows, mapping, institution):
    """Check every row of the file.

    Returns one result per data row: the values that were read, the problems
    found and the row number the user sees in the spreadsheet.
    """
    fields = FIELDS_BY_KIND[kind]
    context = Context(institution)
    context.load()
    seen_numbers = {}
    seen_usernames = {}
    seen_emails = {}
    results = []
    for index, row in enumerate(rows, start=2):
        errors = []
        values = {}
        for field in fields:
            column = mapping.get(field["key"])
            if not column:
                if field["required"]:
                    errors.append(f"{field['label']} is missing.")
                continue
            raw = _clean(row.get(column, ""))
            if not raw:
                if field["required"]:
                    errors.append(f"{field['label']} is needed.")
                continue
            values[field["key"]] = raw
        checker = CHECKERS[kind]
        values, errors = checker(
            values, errors, context, seen_numbers, seen_usernames, seen_emails)
        results.append({
            "row": index,
            "values": values,
            "errors": errors,
            "raw": row,
        })
    return results


def _valid_email(value):
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value or ""))


def _check_email(values, errors, context, seen_emails):
    email = (values.get("email") or "").strip().lower()
    if not email:
        return ""
    if not _valid_email(email):
        errors.append(f"'{values.get('email')}' is not an email address.")
        return email
    if email in seen_emails:
        errors.append(f"Email '{email}' is used twice in this file.")
    elif context.taken_email(email):
        errors.append(
            f"An account at this institution already uses the email '{email}'."
        )
    seen_emails[email] = True
    return email


def _check_students(values, errors, context, seen_numbers, seen_usernames, seen_emails):
    from academics.models import Student
    from accounts.models import User
    from core.numbering import number_key

    student_no = str(values.get("student_no", "") or "").strip().upper()
    values["student_no"] = student_no
    username = values.get("username", "")
    if student_no:
        key = number_key(student_no)
        if key in seen_numbers:
            errors.append(f"Student number '{student_no}' is used twice in this file.")
        seen_numbers[key] = True
        existing = Student.all_objects.filter(
            institution=context.institution).values_list("student_no", flat=True)
        if any(number_key(number) == key for number in existing):
            errors.append(f"Student number '{student_no}' is already used by another student.")
    if not username:
        username = re.sub(r"[^a-zA-Z0-9._-]", "", student_no) or f"student{len(seen_usernames) + 1}"
        values["username"] = username
    if username:
        if username.lower() in seen_usernames:
            errors.append(f"Username '{username}' is used twice in this file.")
        elif User.objects.filter(username__iexact=username).exists():
            errors.append(f"Username '{username}' is already taken.")
        seen_usernames[username.lower()] = True
    values["email"] = _check_email(values, errors, context, seen_emails)
    if values.get("programme"):
        if context.programme(values["programme"]) is None:
            errors.append(f"Unknown programme '{values['programme']}'.")
    school_class = None
    if values.get("school_class"):
        school_class = context.school_class(values["school_class"])
        if school_class is None:
            errors.append(f"Unknown class '{values['school_class']}'.")
    if values.get("stream"):
        if context.stream(school_class, values["stream"]) is None:
            errors.append(f"Unknown stream '{values['stream']}'.")
    if values.get("date_of_birth"):
        values["date_of_birth"] = parse_date(
            values["date_of_birth"], "Date of birth", errors)
    if values.get("status"):
        values["status"] = parse_choice(
            values["status"], "Status", Student.Status.values, errors,
            default=Student.Status.ACTIVE)
    return values, errors


def _check_staff(values, errors, context, seen_numbers, seen_usernames, seen_emails):
    from accounts.models import User
    from core.models import StaffProfile
    from core.numbering import number_key

    employee_no = str(values.get("employee_no", "") or "").strip().upper()
    values["employee_no"] = employee_no
    if employee_no:
        key = number_key(employee_no)
        if key in seen_numbers:
            errors.append(f"Employee number '{employee_no}' is used twice in this file.")
        seen_numbers[key] = True
        existing = StaffProfile.all_objects.filter(
            institution=context.institution).values_list("employee_no", flat=True)
        if any(number_key(number) == key for number in existing):
            errors.append(
                f"Employee number '{employee_no}' is already used by another staff member.")
    username = values.get("username", "")
    if username:
        if username.lower() in seen_usernames:
            errors.append(f"Username '{username}' is used twice in this file.")
        elif User.objects.filter(username__iexact=username).exists():
            errors.append(f"Username '{username}' is already taken.")
        seen_usernames[username.lower()] = True
    values["email"] = _check_email(values, errors, context, seen_emails)
    if values.get("date_joined"):
        values["date_joined"] = parse_date(values["date_joined"], "Date joined", errors)
    if values.get("role"):
        role = _staff_role(context, values["role"])
        if role is None:
            errors.append(f"Unknown role '{values['role']}'.")
        elif role.code in ("student", "parent"):
            errors.append(f"'{role.name}' is not a staff role.")
        else:
            values["role"] = role.code
    return values, errors


def _staff_role(context, value):
    """Resolve a role by code, name, or an older ``User.role`` value."""
    from core.roles import LEGACY_ROLE_MAP

    role = context.role(value)
    if role is not None:
        return role
    key = (value or "").strip().lower()
    return context.role(LEGACY_ROLE_MAP.get(key, key))


def _check_courses(values, errors, context, seen_numbers, seen_usernames, seen_emails):
    from academics.models import Course

    code = values.get("code", "")
    if code:
        if code in seen_numbers:
            errors.append(f"Course code '{code}' is used twice in this file.")
        seen_numbers[code] = True
        if Course.all_objects.filter(code=code).exists():
            errors.append(f"Course code '{code}' is already used.")
    if values.get("programme"):
        if context.programme(values["programme"]) is None:
            errors.append(f"Unknown programme '{values['programme']}'.")
    if values.get("credits"):
        values["credits"] = parse_number(values["credits"], "Credits", errors)
    if values.get("lecturer"):
        from accounts.models import User

        if not User.objects.filter(username=values["lecturer"]).exists():
            errors.append(f"There is no user called '{values['lecturer']}'.")
    return values, errors


def _check_subjects(values, errors, context, seen_numbers, seen_usernames, seen_emails):
    from academics.models import Subject

    code = values.get("code", "")
    if code:
        if code in seen_numbers:
            errors.append(f"Subject code '{code}' is used twice in this file.")
        seen_numbers[code] = True
        if Subject.all_objects.filter(code=code).exists():
            errors.append(f"Subject code '{code}' is already used.")
    if values.get("level"):
        values["level"] = parse_choice(
            values["level"], "Level", Subject.Level.values, errors,
            default=Subject.Level.SECONDARY)
    if values.get("lecturer"):
        from accounts.models import User

        if not User.objects.filter(username=values["lecturer"]).exists():
            errors.append(f"There is no user called '{values['lecturer']}'.")
    return values, errors


def _check_fee_structures(values, errors, context, seen_numbers, seen_usernames, seen_emails):
    from finance.models import FeeStructure

    code = values.get("structure_code", "")
    if code and FeeStructure.all_objects.filter(code=code).exists():
        errors.append(f"Fee structure code '{code}' is already used.")
    if values.get("amount"):
        values["amount"] = parse_money(values["amount"], "Amount", errors)
    if values.get("mandatory"):
        values["mandatory"] = parse_yes_no(
            values["mandatory"], "Mandatory", errors, default=True)
    if values.get("academic_year"):
        if context.academic_year(values["academic_year"]) is None:
            errors.append(f"Unknown academic year '{values['academic_year']}'.")
    return values, errors


CHECKERS = {
    "students": _check_students,
    "staff": _check_staff,
    "courses": _check_courses,
    "subjects": _check_subjects,
    "fee_structures": _check_fee_structures,
}


# ---------- writing the rows ----------
def run_import(kind, results, institution, user, access_mode=None, batch=None):
    """Write the rows that passed the check.

    Returns (imported, errors). A row that fails while being written is
    reported like any other problem, so the user gets one list of errors.

    When ``access_mode`` is given, accounts are created through the same
    services as the Users area, so an invitation or a one-time password is
    produced. Without it the historical import behaviour is kept: the login is
    created with an unusable password. Each row is written in its own
    transaction, so one broken row never rolls back the good ones.
    """
    from core.tenancy import use_institution

    imported = 0
    errors = []
    created_ids = list(getattr(batch, "created_user_ids", []) or [])
    credentials = list(getattr(batch, "credentials", []) or [])
    with use_institution(institution):
        for result in results:
            if result["errors"]:
                errors.append({"row": result["row"], "messages": result["errors"]})
                continue
            try:
                with transaction.atomic():
                    info = _write_row(
                        kind, result["values"], institution, user, access_mode)
                imported += 1
                if info:
                    created_ids.append(info["user_id"])
                    credentials.append(info)
            except Exception as exc:  # a row that breaks must not stop the file
                errors.append({"row": result["row"], "messages": [str(exc)]})
    if batch is not None:
        batch.created_user_ids = created_ids
        batch.credentials = credentials
        batch.save(update_fields=["created_user_ids", "credentials"])
    return imported, errors


def _write_row(kind, values, institution, user, access_mode=None):
    writer = WRITERS[kind]
    if access_mode and kind in ("students", "staff"):
        writer = ACCESS_WRITERS[kind]
        return writer(values, institution, user, access_mode)
    writer(values, institution, user)
    return None


def _credential_info(account, student=None):
    user = account.user
    return {
        "user_id": user.pk,
        "username": user.username,
        "email": user.email,
        "name": user.get_full_name() or user.username,
        "password": account.password or "",
        "access_mode": account.access_mode,
        "student_no": getattr(student, "student_no", ""),
    }


def _write_student(values, institution, user):
    from academics.models import Student
    from accounts.models import User

    context = Context(institution)
    account = User.objects.create_user(
        username=values["username"],
        password=None,
        first_name=values.get("first_name", "")[:60],
        last_name=values.get("last_name", "")[:60],
        email=values.get("email", ""),
        role=User.Role.STUDENT,
        institution=institution,
    )
    account.set_unusable_password()
    account.save(update_fields=["password"])
    programme = context.programme(values.get("programme", ""))
    school_class = context.school_class(values.get("school_class", "")) if values.get("school_class") else None
    stream = context.stream(school_class, values.get("stream", "")) if values.get("stream") else None
    student = Student(
        user=account,
        student_no=values.get("student_no", "")[:20],
        programme=programme,
        school_class=school_class,
        stream=stream,
        year_or_grade=values.get("year_or_grade", "")[:20] or "1",
        date_of_birth=values.get("date_of_birth"),
        guardian_name=values.get("guardian_name", "")[:120],
        guardian_phone=values.get("guardian_phone", "")[:30],
        status=values.get("status") or Student.Status.ACTIVE,
        institution=institution,
    )
    student.save()


def _write_staff(values, institution, user):
    from accounts.models import User
    from core.models import StaffProfile

    account = User.objects.create_user(
        username=values["username"],
        password=None,
        first_name=values.get("first_name", "")[:150],
        last_name=values.get("last_name", "")[:150],
        email=values.get("email", ""),
        phone=values.get("phone", "")[:30],
        role=values.get("role") or User.Role.LECTURER,
        institution=institution,
    )
    account.set_unusable_password()
    account.save(update_fields=["password"])
    department = None
    if values.get("department"):
        from core.models import Department

        department = Department.objects.filter(
            name__iexact=values["department"]).first()
    profile = StaffProfile(
        institution=institution,
        user=account,
        employee_no=values.get("employee_no", "")[:30],
        job_title=values.get("job_title", "")[:120],
        phone=values.get("phone", "")[:30],
        date_joined=values.get("date_joined"),
    )
    if department is not None:
        profile._department_code = department.code
    profile.save()
    if department is not None:
        profile.departments.add(department)


def _write_student_with_access(values, institution, user, access_mode):
    """Create a student through the same service that admissions uses."""
    from academics.models import Student
    from admissions.services import create_student_record

    context = Context(institution)
    programme = context.programme(values.get("programme", ""))
    if programme is None:
        raise ValueError(f"Unknown programme '{values.get('programme', '')}'.")
    school_class = (
        context.school_class(values.get("school_class", ""))
        if values.get("school_class") else None
    )
    stream = (
        context.stream(school_class, values.get("stream", ""))
        if values.get("stream") else None
    )
    result = create_student_record(
        institution,
        first_name=values.get("first_name", ""),
        last_name=values.get("last_name", ""),
        email=values.get("email", ""),
        phone=values.get("phone", ""),
        username=values.get("username", ""),
        programme=programme,
        school_class=school_class,
        stream=stream,
        year_or_grade=values.get("year_or_grade", "") or "1",
        date_of_birth=values.get("date_of_birth"),
        guardian_name=values.get("guardian_name", ""),
        guardian_phone=values.get("guardian_phone", ""),
        student_no=values.get("student_no", ""),
        access_mode=access_mode,
        actor=user,
        status=values.get("status") or Student.Status.ACTIVE,
    )
    return _credential_info(result["account"], result["student"])


def _write_staff_with_access(values, institution, user, access_mode):
    """Create a staff login, profile and role through the Users services."""
    from accounts.user_admin import check_role_allowed, legacy_role_for, provision_user
    from core.models import Department, Role, StaffProfile

    context = Context(institution)
    role = context.role(values.get("role", ""))
    if role is None and values.get("role"):
        role = Role.all_objects.filter(
            institution=institution, code=values["role"]
        ).first()
    if role is not None:
        check_role_allowed(user, role)
    account = provision_user(
        institution,
        first_name=values.get("first_name", ""),
        last_name=values.get("last_name", ""),
        email=values.get("email", ""),
        phone=values.get("phone", ""),
        username=values.get("username", ""),
        role=role,
        access_mode=access_mode,
        actor=user,
    )
    profile = StaffProfile.objects.create(
        institution=institution,
        user=account.user,
        employee_no=values.get("employee_no", "")[:30],
        job_title=values.get("job_title", "")[:120],
        phone=values.get("phone", "")[:30],
        date_joined=values.get("date_joined"),
    )
    if values.get("department"):
        department = Department.all_objects.filter(
            institution=institution, name__iexact=values["department"]
        ).first()
        if department is not None:
            profile.departments.add(department)
    if role is not None:
        account.user.role = legacy_role_for(role)
        account.user.save(update_fields=["role"])
    return _credential_info(account)


def undo_import(batch, actor=None, request=None):
    """Deactivate the accounts of a batch that nobody has signed in to yet.

    A login that has been used is left alone, and no financial, academic or
    audit history is ever deleted: undo deactivates, it does not erase.
    """
    from django.utils import timezone

    from accounts.models import User
    from accounts.user_admin import deactivate_user
    from core.audit import log_action

    undone = 0
    skipped = []
    for user_id in batch.created_user_ids or []:
        account = User.objects.filter(
            pk=user_id, institution=batch.institution
        ).first()
        if account is None:
            continue
        if account.last_login is not None or not account.is_active:
            skipped.append(account.username)
            continue
        deactivate_user(
            account,
            actor=actor,
            request=request,
            reason=f"Undo of import {batch.pk}",
        )
        undone += 1
    batch.undone_at = timezone.now()
    batch.undone_count = undone
    batch.save(update_fields=["undone_at", "undone_count"])
    log_action(
        actor,
        "import.undone",
        batch,
        request=request,
        after={"undone": undone, "skipped": len(skipped)},
    )
    return undone, skipped


def _write_course(values, institution, user):
    from academics.models import Course
    from accounts.models import User

    context = Context(institution)
    programme = context.programme(values.get("programme", ""))
    if programme is None:
        raise ValueError(f"Unknown programme '{values.get('programme', '')}'.")
    lecturer = None
    if values.get("lecturer"):
        lecturer = User.objects.filter(username=values["lecturer"]).first()
    course = Course(
        institution=institution,
        code=values.get("code", "")[:20],
        title=values.get("title", "")[:150],
        programme=programme,
        credits=int(values.get("credits") or 3),
        lecturer=lecturer,
    )
    course.save()


def _write_subject(values, institution, user):
    from academics.models import Subject
    from accounts.models import User

    lecturer = None
    if values.get("lecturer"):
        lecturer = User.objects.filter(username=values["lecturer"]).first()
    subject = Subject(
        institution=institution,
        code=values.get("code", "")[:20],
        name=values.get("name", "")[:150],
        level=values.get("level") or Subject.Level.SECONDARY,
        lecturer=lecturer,
    )
    subject.save()


def _write_fee_structure(values, institution, user):
    from finance.models import FeeItem, FeeStructure

    context = Context(institution)
    structure, _created = FeeStructure.objects.get_or_create(
        institution=institution,
        code=values.get("structure_code", "")[:30],
        defaults={
            "name": values.get("structure_name", "")[:120],
            "academic_year": context.academic_year(values.get("academic_year", "")),
        },
    )
    item, created = FeeItem.objects.get_or_create(
        institution=institution,
        structure=structure,
        name=values.get("item_name", "")[:120],
        defaults={
            "amount": values.get("amount") or Decimal("0.00"),
            "is_mandatory": values.get("mandatory", True),
        },
    )
    if not created:
        item.amount = values.get("amount") or item.amount
        item.is_mandatory = values.get("mandatory", item.is_mandatory)
        item.save(update_fields=["amount", "is_mandatory"])


WRITERS = {
    "students": _write_student,
    "staff": _write_staff,
    "courses": _write_course,
    "subjects": _write_subject,
    "fee_structures": _write_fee_structure,
}

ACCESS_WRITERS = {
    "students": _write_student_with_access,
    "staff": _write_staff_with_access,
}
