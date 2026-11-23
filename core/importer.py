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

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import models, transaction

from accounts.usernames import (
    normalize_email as normalize_email_value,
)
from accounts.usernames import (
    normalize_identifier,
    reserve_username,
)

MAX_ROWS = 5000
MAX_BYTES = 5 * 1024 * 1024


def max_rows():
    """The configured row limit, read at call time so settings can override it."""
    return int(getattr(settings, "BULK_UPLOAD_MAX_ROWS", MAX_ROWS))


def max_bytes():
    """The configured upload size limit in bytes."""
    return int(getattr(settings, "BULK_UPLOAD_MAX_BYTES", MAX_BYTES))

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

# The columns of the dedicated student bulk-upload. A row can hold several
# courses in one cell, or use the extra course columns for one course each.
BULK_STUDENT_FIELDS = [
    _field("registration_number", "Registration number",
           aliases=("registration number", "registration no", "reg number", "reg no",
                    "student number", "student no", "admission number", "student id")),
    _field("first_name", "First name", required=True,
           aliases=("first name", "given name", "firstname", "name of student")),
    _field("last_name", "Surname", required=True,
           aliases=("surname", "last name", "family name", "lastname")),
    _field("date_of_birth", "Date of birth", kind=DATE,
           aliases=("date of birth", "dob", "birth date", "birthday")),
    _field("gender", "Gender", kind=CHOICE,
           aliases=("gender", "sex"), choices=("female", "male", "other", "unspecified")),
    _field("email", "Email", aliases=("email", "email address", "student email", "e mail")),
    _field("phone", "Phone", aliases=("phone", "mobile", "cell", "student phone",
                                      "phone number")),
    _field("programme", "Programme or class code or name",
           aliases=("programme", "program", "programme code", "programme name",
                    "class", "class code", "class name", "course of study", "level")),
    _field("year_or_grade", "Year or grade",
           aliases=("year or grade", "year/grade", "year", "grade", "form", "class year")),
    _field("stream_or_cohort", "Stream or cohort",
           aliases=("stream or cohort", "stream", "cohort", "stream name", "cohort name")),
    _field("intake_year", "Intake or admission year",
           aliases=("intake or admission year", "intake", "intake year",
                    "admission year", "academic year", "year of entry")),
    _field("guardian_name", "Guardian name",
           aliases=("guardian name", "guardian", "parent", "parent name")),
    _field("guardian_phone", "Guardian phone",
           aliases=("guardian phone", "parent phone", "next of kin phone")),
    _field("courses", "Course codes or names",
           aliases=("course codes or names", "courses", "course", "course code",
                    "course codes", "course name", "subjects", "subject codes"),
           help_text="Separate several courses with semicolons, or use the extra course columns."),
]
for _course_column in range(1, 6):
    BULK_STUDENT_FIELDS.append(
        _field(
            f"course_{_course_column}",
            f"Course {_course_column}",
            aliases=(f"course {_course_column}", f"course {_course_column} code",
                     f"course {_course_column} name", f"subject {_course_column}"),
            help_text="Optional: one course per column instead of a semicolon list.",
        )
    )
BULK_STUDENT_FIELDS.extend([
    _field("term", "Term", aliases=("term", "term name", "semester")),
    _field("status", "Status", kind=CHOICE, aliases=("status", "student status"),
           choices=("active", "enrolled", "deferred", "suspended", "transferred_out",
                    "withdrawn", "graduated", "alumni")),
    _field("username", "Username", aliases=("username", "login", "user name",
                                            "login username")),
])

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

# The academic structure sheets. Codes are matched before names so a renamed
# department keeps its history; a blank code falls back to the name. Rows are
# only ever created or updated, never deleted.
SCHOOL_FIELDS = [
    _field("code", "Code", required=True, aliases=("school code", "faculty code")),
    _field("name", "Name", required=True,
           aliases=("school name", "faculty name", "school", "faculty")),
    _field("description", "Description", aliases=("description", "details")),
    _field("head", "Head", aliases=("head", "head of school", "head of faculty", "dean")),
    _field("parent", "Parent school or faculty",
           aliases=("parent", "parent school", "parent faculty")),
    _field("active", "Active", kind=CHOICE, choices=("yes", "no"),
           aliases=("active", "is active")),
    _field("sort_order", "Sort order", kind=NUMBER, aliases=("sort order", "order")),
]

DEPARTMENT_IMPORT_FIELDS = [
    _field("code", "Code", required=True, aliases=("department code", "dept code")),
    _field("name", "Name", required=True,
           aliases=("department name", "department", "dept")),
    _field("school", "School or faculty",
           aliases=("school", "faculty", "school code", "faculty code")),
    _field("description", "Description", aliases=("description", "details")),
    _field("active", "Active", kind=CHOICE, choices=("yes", "no"),
           aliases=("active", "is active")),
    _field("sort_order", "Sort order", kind=NUMBER, aliases=("sort order", "order")),
]

GRADE_LEVEL_IMPORT_FIELDS = [
    _field("name", "Name", required=True,
           aliases=("grade level", "level", "grade", "form", "year")),
    _field("code", "Code", aliases=("grade code", "level code")),
    _field("kind", "Level type", kind=CHOICE, choices=("primary", "secondary", "college"),
           aliases=("level type", "type", "kind")),
    _field("order", "Order", kind=NUMBER, aliases=("order", "sort order")),
    _field("promotion_to", "Promotion target",
           aliases=("promotion target", "promotion to", "next level")),
    _field("active", "Active", kind=CHOICE, choices=("yes", "no"),
           aliases=("active", "is active")),
]

CLASS_IMPORT_FIELDS = [
    _field("name", "Name", required=True, aliases=("class", "class name", "form", "grade")),
    _field("grade_level", "Grade level", aliases=("grade level", "level", "grade", "form")),
    _field("school", "School or faculty", aliases=("school", "faculty")),
    _field("department", "Department", aliases=("department", "dept")),
    _field("streams", "Streams",
           aliases=("stream", "streams"),
           help_text="Separate several streams with semicolons or commas."),
    _field("class_teacher", "Class teacher", aliases=("class teacher", "teacher", "form teacher")),
    _field("capacity", "Capacity", kind=NUMBER, aliases=("capacity", "max students")),
    _field("room", "Room", aliases=("room", "classroom")),
    _field("active", "Active", kind=CHOICE, choices=("yes", "no"),
           aliases=("active", "is active")),
]


KINDS = {
    "students": {
        "label": "Students",
        "permission": "students.create",
        "fields": STUDENT_FIELDS,
        "help": "One row per student. First name and last name are needed.",
    },
    "student_upload": {
        "label": "Bulk upload students",
        "permission": "students.bulk_import",
        "fields": BULK_STUDENT_FIELDS,
        "help": "Create and update students, their classes, years and course enrolments.",
    },
    "staff": {
        "label": "Staff",
        "permission": "admin.manage_users",
        "fields": STAFF_FIELDS,
        "help": "One row per staff member. A login is created and sign-in instructions are sent or shown.",
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
    "schools": {
        "label": "Schools or Faculties",
        "permission": "academics.manage_structure",
        "fields": SCHOOL_FIELDS,
        "sheet": "Schools or Faculties",
        "help": "One row per school, faculty or division. Codes identify rows across imports.",
    },
    "departments": {
        "label": "Departments",
        "permission": "academics.manage_structure",
        "fields": DEPARTMENT_IMPORT_FIELDS,
        "sheet": "Departments",
        "help": "One row per department. A school or faculty is optional.",
    },
    "grade_levels": {
        "label": "Grade levels",
        "permission": "academics.manage_structure",
        "fields": GRADE_LEVEL_IMPORT_FIELDS,
        "sheet": "Grade levels",
        "help": "One row per grade, form or year. Order controls promotion.",
    },
    "classes": {
        "label": "Classes",
        "permission": "academics.manage_structure",
        "fields": CLASS_IMPORT_FIELDS,
        "sheet": "Classes",
        "help": "One row per class, with its grade level, school, streams and teacher.",
    },
}


def kind_fields(kind):
    """The fields of one import kind."""
    return KINDS[kind]["fields"]


FIELDS_BY_KIND = {key: spec["fields"] for key, spec in KINDS.items()}


def kind_label(kind):
    return KINDS[kind]["label"]


# ---------- reading the file ----------
def read_rows(uploaded_file, filename, kind=None):
    """Read an uploaded file into a list of rows.

    The first row is the header. Every row is a dictionary of column name to
    value, so the rest of the import never has to think about columns again.
    For an Excel workbook, the sheet named for the chosen import kind is used
    when it is present; otherwise the first sheet is read as before. This lets
    one workbook carry all four academic structure sheets.
    """
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        spec = KINDS.get(kind or "", {})
        raw = _read_xlsx(
            uploaded_file,
            sheet=spec.get("sheet") or ("Students" if kind == "student_upload" else None),
        )
    elif name.endswith(".csv"):
        raw = _read_csv(uploaded_file)
    else:
        raise ImportError("Upload a CSV file or an Excel workbook (.xlsx).")
    if not raw:
        raise ImportError("The file is empty.")
    # Keep each header attached to its original column. Dropping duplicate or
    # blank cells and re-indexing from zero shifts values into the wrong fields
    # in real-world spreadsheets (for example two "Name" columns).
    columns = []
    used = set()
    for index, cell in enumerate(raw[0]):
        header = str(cell or "").strip()
        if not header:
            continue
        if header in used:
            suffix = 2
            while f"{header} ({suffix})" in used:
                suffix += 1
            header = f"{header} ({suffix})"
        used.add(header)
        columns.append((index, header))
    if not columns:
        raise ImportError("The first row of the file must hold the column names.")
    headers = [header for _index, header in columns]
    rows = []
    for raw_row in raw[1:]:
        if not any(str(cell or "").strip() for cell in raw_row):
            continue
        row = {}
        for index, header in columns:
            cell = raw_row[index] if index < len(raw_row) else ""
            row[header] = "" if cell is None else str(cell).strip()
        rows.append(row)
        if len(rows) > max_rows():
            raise ImportError(
                f"The file has more than {max_rows()} rows. Split it into smaller files.")
    return [{header: header for header in headers}] + rows


def _read_csv(uploaded_file):
    uploaded_file.seek(0)
    raw = uploaded_file.read()
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ImportError("The CSV file must be saved as UTF-8.") from exc
    sample = raw[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(raw), dialect)
    return [list(row) for row in reader]


def _read_xlsx(uploaded_file, sheet=None):
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise ImportError("Excel files need the openpyxl library. Install it or upload a CSV.") from exc
    uploaded_file.seek(0)
    signature = uploaded_file.read(4)
    uploaded_file.seek(0)
    if signature[:2] != b"PK":
        raise ImportError("That file is not an Excel workbook. Upload a real .xlsx file.")
    try:
        book = load_workbook(uploaded_file, read_only=True, data_only=True)
    except Exception as exc:
        raise ImportError("That Excel file could not be opened.") from exc
    if sheet and sheet in book.sheetnames:
        chosen = book[sheet]
    else:
        chosen = book[book.sheetnames[0]]
    rows = []
    for row in chosen.iter_rows(values_only=True):
        rows.append(["" if cell is None else cell for cell in row])
    book.close()
    return rows


# ---------- mapping the columns ----------
def _header_key(value):
    """A forgiving comparison key: "Date_Of_Birth" becomes "date of birth"."""
    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
    return re.sub(r"\s+", " ", text)


def guess_mapping(headers, fields):
    """Guess which column belongs to which field.

    Header names are compared case-insensitively and punctuation is ignored, so
    "Reg No", "reg_no" and "Registration Number" all find the same column. The
    administrator can still change any guess on the mapping screen.
    """
    mapping = {}
    taken = set()
    prepared = [
        (field, {_header_key(item) for item in
                 [field["label"], field["key"].replace("_", " "), *field["aliases"]]})
        for field in fields
    ]
    for field, wanted in prepared:
        for header in headers:
            if header not in taken and _header_key(header) in wanted:
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
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d",
                    "%d/%m/%y", "%d %b %Y", "%d %B %Y", "%b %d %Y"):
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
        self.schools_by_code = {}
        self.schools_by_name = {}
        self.departments_by_code = {}
        self.departments_by_name = {}
        self.levels_by_code = {}
        self.levels_by_name = {}
        self._student_numbers = None
        self._employee_numbers = None
        self._usernames = None
        self._staff_emails = None
        self._loaded = False

    def load(self):
        if self._loaded:
            return
        from academics.models import (
            AcademicSchool,
            AcademicYear,
            GradeLevel,
            Programme,
            SchoolClass,
            Stream,
        )
        from core.models import Department, Role

        for row in AcademicSchool.all_objects.filter(institution=self.institution):
            if row.code:
                self.schools_by_code[row.code.strip().lower()] = row
            self.schools_by_name[row.name.strip().lower()] = row
        for row in Department.all_objects.filter(institution=self.institution):
            if row.code:
                self.departments_by_code[row.code.strip().lower()] = row
            self.departments_by_name[row.name.strip().lower()] = row
        for row in GradeLevel.all_objects.filter(institution=self.institution):
            if row.code:
                self.levels_by_code[row.code.strip().lower()] = row
            self.levels_by_name[row.name.strip().lower()] = row
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
        from accounts.models import ReservedUsername, User
        from accounts.usernames import _username_key

        # User.objects is intentionally unfiltered (tenant_objects is the
        # filtered one), so it is the correct manager for cross-tenant checks.
        self._usernames = {
            _username_key(name)
            for name in User.objects.filter(
                institution=self.institution
            ).values_list("username", flat=True)
        }
        self._usernames.update(
            _username_key(name)
            for name in ReservedUsername.objects.values_list("username", flat=True)
        )
        self._staff_emails = {
            email.strip().lower()
            for email in User.objects.filter(
                institution=self.institution, is_active=True
            )
            .exclude(role__in=(User.Role.STUDENT, User.Role.PARENT))
            .values_list("email", flat=True)
            if email
        }
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

    @staticmethod
    def _find(by_code, by_name, value):
        """Match a reference by code first, then by name; empty means none."""
        key = (value or "").strip().lower()
        if not key:
            return None
        return by_code.get(key) or by_name.get(key)

    def school(self, value):
        self.load()
        return self._find(self.schools_by_code, self.schools_by_name, value)

    def department(self, value):
        self.load()
        return self._find(self.departments_by_code, self.departments_by_name, value)

    def level(self, value):
        self.load()
        return self._find(self.levels_by_code, self.levels_by_name, value)

    def user(self, value):
        """A staff account, matched by login username or email in this tenant."""
        from accounts.models import User

        key = (value or "").strip().lower()
        if not key:
            return None
        return User.objects.filter(institution=self.institution).filter(
            models.Q(username__iexact=key) | models.Q(email__iexact=key)
        ).first()

    def student_numbers(self):
        from academics.models import Student
        from core.numbering import number_key

        if self._student_numbers is None:
            self._student_numbers = {
                number_key(number)
                for number in Student.all_objects.filter(
                    institution=self.institution
                ).values_list("student_no", flat=True)
            }
        return self._student_numbers

    def employee_numbers(self):
        from core.models import StaffProfile
        from core.numbering import number_key

        if self._employee_numbers is None:
            self._employee_numbers = {
                number_key(number)
                for number in StaffProfile.all_objects.filter(
                    institution=self.institution
                ).values_list("employee_no", flat=True)
            }
        return self._employee_numbers

    def taken_username(self, username):
        from accounts.usernames import _username_key

        self.load()
        return bool(username) and _username_key(username) in self._usernames

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

    def taken_staff_email(self, email, exclude_pk=None):
        if not email:
            return False
        # exclude_pk is not used by the import checkers, but keep the signature
        # compatible with the other lookup helpers.
        self.load()
        return email.strip().lower() in self._staff_emails


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
    try:
        validate_email(value)
    except ValidationError:
        return False
    return True


def _check_email(values, errors, context, seen_emails, staff_only=False):
    email = normalize_email_value(values.get("email") or "")
    if not email:
        return ""
    if not _valid_email(email):
        errors.append(f"'{values.get('email')}' is not an email address.")
        return email
    key = email
    if key in seen_emails:
        errors.append(f"Email '{email}' is used twice in this file.")
    else:
        if staff_only and context.taken_staff_email(email):
            errors.append(
                f"An account at this institution already uses the email '{email}'."
            )
    seen_emails[key] = True
    return email


def _check_students(values, errors, context, seen_numbers, seen_usernames, seen_emails):
    from academics.models import Student
    from core.numbering import number_key

    existing_numbers = context.student_numbers()
    student_no = str(values.get("student_no", "") or "").strip().upper()
    values["student_no"] = student_no
    if student_no:
        key = number_key(student_no)
        if key in seen_numbers:
            errors.append(f"Student number '{student_no}' is used twice in this file.")
        seen_numbers[key] = True
        if key in existing_numbers:
            errors.append(f"Student number '{student_no}' is already used by another student.")

    email = _check_email(values, errors, context, seen_emails, staff_only=False)
    values["email"] = email

    username = normalize_identifier(values.get("username", "")).lower()
    if not username:
        # Keep the historic student-number based login for bulk imports. The
        # optional username field on the admin forms uses email instead.
        username = re.sub(
            r"[^a-zA-Z0-9._-]", "", student_no
        ) or f"student{len(seen_usernames) + 1}"
    key = username.lower()
    if key in seen_usernames:
        errors.append(f"Username '{username}' is used twice in this file.")
    elif context.taken_username(username):
        errors.append(f"Username '{username}' is already taken.")
    seen_usernames[key] = True
    values["username"] = username

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
    from core.numbering import number_key

    existing_numbers = context.employee_numbers()
    employee_no = str(values.get("employee_no", "") or "").strip().upper()
    values["employee_no"] = employee_no
    if employee_no:
        key = number_key(employee_no)
        if key in seen_numbers:
            errors.append(f"Employee number '{employee_no}' is used twice in this file.")
        seen_numbers[key] = True
        if key in existing_numbers:
            errors.append(
                f"Employee number '{employee_no}' is already used by another staff member.")

    values["email"] = _check_email(values, errors, context, seen_emails, staff_only=True)

    username = normalize_identifier(values.get("username", "")).lower()
    if username:
        key = username.lower()
        if key in seen_usernames:
            errors.append(f"Username '{username}' is used twice in this file.")
        elif context.taken_username(username):
            errors.append(f"Username '{username}' is already taken.")
        seen_usernames[key] = True
        values["username"] = username

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
        if Course.all_objects.filter(institution=context.institution, code=code).exists():
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


def _check_bulk_students(values, errors, context, seen_numbers, seen_usernames, seen_emails):
    """Basic column checks; the full matching plan lives in ``core.bulk_upload``."""
    from core.bulk_upload import check_row

    return check_row(values, errors, context.institution)


def _reference_key(value):
    """A forgiving key for codes and names used to identify structure rows."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _duplicate(value, seen, label, errors):
    key = _reference_key(value)
    if key and key in seen:
        errors.append(f"{label} '{value}' is used twice in this file.")
    if key:
        seen[key] = True


def _optional_bool(values, key, label, errors):
    if key in values:
        values[key] = parse_yes_no(values[key], label, errors, default=True)


def _optional_number(values, key, label, errors):
    if key in values:
        values[key] = parse_number(values[key], label, errors)


def _check_schools(values, errors, context, seen_numbers, seen_usernames, seen_emails):
    _duplicate(values.get("code") or values.get("name"), seen_numbers,
               "School code", errors)
    if values.get("parent"):
        parent = context.school(values["parent"])
        if parent is None:
            errors.append(f"Unknown parent school '{values['parent']}'.")
        else:
            values["parent_id"] = parent.pk
    if values.get("head"):
        head = context.user(values["head"])
        if head is None:
            errors.append(f"There is no user called '{values['head']}' at this institution.")
        else:
            values["head_id"] = head.pk
    _optional_bool(values, "active", "Active", errors)
    _optional_number(values, "sort_order", "Sort order", errors)
    return values, errors


def _check_departments(values, errors, context, seen_numbers, seen_usernames, seen_emails):
    _duplicate(values.get("code") or values.get("name"), seen_numbers,
               "Department code", errors)
    if values.get("school"):
        school = context.school(values["school"])
        if school is None:
            errors.append(f"Unknown school or faculty '{values['school']}'.")
        else:
            values["school_id"] = school.pk
    _optional_bool(values, "active", "Active", errors)
    _optional_number(values, "sort_order", "Sort order", errors)
    return values, errors


def _check_grade_levels(values, errors, context, seen_numbers, seen_usernames, seen_emails):
    from academics.models import GradeLevel

    _duplicate(values.get("code") or values.get("name"), seen_numbers,
               "Grade level", errors)
    if values.get("kind"):
        values["kind"] = parse_choice(
            values["kind"], "Level type", GradeLevel.Kind.values, errors,
            default=GradeLevel.Kind.SECONDARY)
    if values.get("order"):
        values["order"] = parse_number(values["order"], "Order", errors)
    if values.get("promotion_to"):
        target = context.level(values["promotion_to"])
        if target is None:
            errors.append(f"Unknown promotion target '{values['promotion_to']}'.")
        else:
            values["promotion_to_id"] = target.pk
    _optional_bool(values, "active", "Active", errors)
    return values, errors


def _check_classes(values, errors, context, seen_numbers, seen_usernames, seen_emails):
    _duplicate(values.get("name"), seen_numbers, "Class", errors)
    if values.get("grade_level"):
        level = context.level(values["grade_level"])
        if level is None:
            errors.append(f"Unknown grade level '{values['grade_level']}'.")
        else:
            values["grade_level_id"] = level.pk
    if values.get("school"):
        school = context.school(values["school"])
        if school is None:
            errors.append(f"Unknown school or faculty '{values['school']}'.")
        else:
            values["school_id"] = school.pk
    if values.get("department"):
        department = context.department(values["department"])
        if department is None:
            errors.append(f"Unknown department '{values['department']}'.")
        else:
            values["department_id"] = department.pk
    if values.get("class_teacher"):
        teacher = context.user(values["class_teacher"])
        if teacher is None:
            errors.append(
                f"There is no user called '{values['class_teacher']}' at this institution.")
        else:
            values["class_teacher_id"] = teacher.pk
    if values.get("streams"):
        values["streams"] = [
            name.strip()[:30] for name in re.split(r"[;,|\n]+", values["streams"])
            if name.strip()
        ]
    _optional_number(values, "capacity", "Capacity", errors)
    _optional_bool(values, "active", "Active", errors)
    return values, errors


CHECKERS = {
    "students": _check_students,
    "student_upload": _check_bulk_students,
    "staff": _check_staff,
    "courses": _check_courses,
    "subjects": _check_subjects,
    "fee_structures": _check_fee_structures,
    "schools": _check_schools,
    "departments": _check_departments,
    "grade_levels": _check_grade_levels,
    "classes": _check_classes,
}


# ---------- writing the rows ----------
def run_import(kind, results, institution, user, access_mode=None, batch=None,
               on_progress=None):
    """Write the rows that passed the check.

    Returns (imported, errors). A row that fails while being written is
    reported like any other problem, so the user gets one list of errors.

    When ``access_mode`` is given, accounts are created through the same
    services as the Users area, so an invitation or a one-time password is
    produced. Without it the historical import behaviour is kept: the login is
    created with an unusable password. Each row is written in its own
    transaction, so one broken row never rolls back the good ones.

    ``on_progress(done, total)`` is called after every row so a background job
    can show how far it has got.
    """
    from core.tenancy import use_institution

    imported = 0
    errors = []
    created_ids = list(getattr(batch, "created_user_ids", []) or [])
    credentials = list(getattr(batch, "credentials", []) or [])
    total = len(results)
    with use_institution(institution):
        for index, result in enumerate(results, start=1):
            if result["errors"]:
                errors.append({"row": result["row"], "messages": result["errors"]})
                if on_progress is not None:
                    on_progress(index, total)
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
            if on_progress is not None:
                on_progress(index, total)
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
    username = normalize_identifier(values.get("username", ""))
    reserve_username(username)
    email = normalize_email_value(values.get("email", ""))
    account = User.objects.create_user(
        username=username,
        password=None,
        first_name=values.get("first_name", "")[:60],
        last_name=values.get("last_name", "")[:60],
        email=email,
        role=User.Role.STUDENT,
        institution=institution,
    )
    account.set_unusable_password()
    account.save(update_fields=["password"])
    reserve_username(account.username)
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

    username = normalize_identifier(values.get("username", "")).lower()
    reserve_username(username)
    email = normalize_email_value(values.get("email", ""))
    if email and User.objects.filter(
        institution=institution, email__iexact=email, is_active=True
    ).exclude(role__in=(User.Role.STUDENT, User.Role.PARENT)).exclude(username__iexact=username).exists():
        raise ValueError(f"An account at this institution already uses the email '{email}'.")
    role_value = values.get("role") or User.Role.LECTURER
    account = User.objects.create_user(
        username=username,
        password=None,
        first_name=values.get("first_name", "")[:150],
        last_name=values.get("last_name", "")[:150],
        email=email,
        phone=values.get("phone", "")[:30],
        role=role_value,
        institution=institution,
    )
    account.set_unusable_password()
    account.save(update_fields=["password"])
    reserve_username(account.username)
    department = None
    if values.get("department"):
        from core.models import Department

        department = Department.all_objects.filter(
            institution=institution, name__iexact=values["department"]
        ).first()
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


def _snapshot(row, fields):
    return {field: getattr(row, field, None) for field in fields}


def _audit_write(user, action, row, before, after, institution):
    from core.audit import log_action

    log_action(user, action, row, before=before, after=after, institution=institution)


def _apply_fields(row, fields, changes):
    for field, value in changes.items():
        if field in fields:
            setattr(row, field, value)


def _write_school(values, institution, user):
    from academics.models import AcademicSchool

    context = Context(institution)
    fields = ("name", "code", "description", "head_id", "parent_id", "is_active", "sort_order")
    row = context.school(values.get("code")) or context.school(values.get("name"))
    before = _snapshot(row, fields) if row else None
    if row is None:
        row = AcademicSchool(institution=institution, name=values.get("name", "")[:120],
                             code=values.get("code", "")[:20])
    changes = {
        "name": values.get("name", "")[:120],
        "description": values.get("description", ""),
    }
    if values.get("code"):
        changes["code"] = values["code"][:20]
    if "head_id" in values:
        changes["head_id"] = values["head_id"]
    if "parent_id" in values:
        changes["parent_id"] = values["parent_id"]
    if "active" in values:
        changes["is_active"] = values["active"]
    if values.get("sort_order") is not None:
        changes["sort_order"] = values["sort_order"]
    _apply_fields(row, fields, changes)
    row.save()
    _audit_write(user, "academic.school.imported", row, before,
                 _snapshot(row, fields), institution)
    return row


def _write_department(values, institution, user):
    from core.models import Department

    context = Context(institution)
    fields = ("name", "code", "school_id", "description", "is_active", "sort_order")
    row = context.department(values.get("code")) or context.department(values.get("name"))
    before = _snapshot(row, fields) if row else None
    if row is None:
        row = Department(institution=institution, name=values.get("name", "")[:120],
                         code=values.get("code", "")[:20])
    changes = {
        "name": values.get("name", "")[:120],
        "description": values.get("description", ""),
    }
    if values.get("code"):
        changes["code"] = values["code"][:20]
    if "school_id" in values:
        changes["school_id"] = values["school_id"]
    if "active" in values:
        changes["is_active"] = values["active"]
    if values.get("sort_order") is not None:
        changes["sort_order"] = values["sort_order"]
    _apply_fields(row, fields, changes)
    row.save()
    _audit_write(user, "academic.department.imported", row, before,
                 _snapshot(row, fields), institution)
    return row


def _write_grade_level(values, institution, user):
    from academics.hierarchy import code_for
    from academics.models import GradeLevel

    context = Context(institution)
    fields = ("name", "code", "kind", "order", "is_active", "promotion_to_id")
    row = context.level(values.get("code")) or context.level(values.get("name"))
    before = _snapshot(row, fields) if row else None
    if row is None:
        order = values.get("order")
        if order is None:
            order = GradeLevel.all_objects.filter(institution=institution).count() + 1
        row = GradeLevel(
            institution=institution,
            name=values.get("name", "")[:60],
            code=(values.get("code") or code_for(values.get("name", "")))[:20],
            order=order,
        )
    changes = {"name": values.get("name", "")[:60]}
    if values.get("code"):
        changes["code"] = values["code"][:20]
    if values.get("kind"):
        changes["kind"] = values["kind"]
    if values.get("order") is not None:
        changes["order"] = values["order"]
    if "active" in values:
        changes["is_active"] = values["active"]
    if "promotion_to_id" in values:
        changes["promotion_to_id"] = values["promotion_to_id"]
    _apply_fields(row, fields, changes)
    row.save()
    _audit_write(user, "academic.grade_level.imported", row, before,
                 _snapshot(row, fields), institution)
    return row


def _write_class(values, institution, user):
    from academics.models import (
        AcademicYear,
        ClassTeacher,
        GradeLevel,
        SchoolClass,
        Stream,
    )

    context = Context(institution)
    fields = ("name", "level", "grade_level_id", "school_id", "department_id",
              "capacity", "room", "is_active")
    row = context.school_class(values.get("name"))
    before = _snapshot(row, fields) if row else None
    if row is None:
        row = SchoolClass(institution=institution, name=values.get("name", "")[:60])
    changes = {"name": values.get("name", "")[:60]}
    if "grade_level_id" in values:
        changes["grade_level_id"] = values["grade_level_id"]
        level = GradeLevel.all_objects.filter(pk=values["grade_level_id"]).first()
        if level is not None:
            changes["level"] = level.kind
    if "school_id" in values:
        changes["school_id"] = values["school_id"]
    if "department_id" in values:
        changes["department_id"] = values["department_id"]
    if values.get("capacity") is not None:
        changes["capacity"] = values["capacity"]
    if "room" in values:
        changes["room"] = values["room"][:60]
    if "active" in values:
        changes["is_active"] = values["active"]
    _apply_fields(row, fields, changes)
    row.save()
    if values.get("streams"):
        for stream_name in values["streams"]:
            Stream.objects.get_or_create(
                institution=institution, school_class=row, name=stream_name)
    if "class_teacher_id" in values:
        year = AcademicYear.objects.filter(institution=institution, is_current=True).first()
        if year is not None:
            ClassTeacher.objects.update_or_create(
                institution=institution, school_class=row, academic_year=year,
                defaults={"teacher_id": values["class_teacher_id"]})
    _audit_write(user, "academic.class.imported", row, before,
                 _snapshot(row, fields), institution)
    return row


WRITERS = {
    "students": _write_student,
    "staff": _write_staff,
    "courses": _write_course,
    "subjects": _write_subject,
    "fee_structures": _write_fee_structure,
    "schools": _write_school,
    "departments": _write_department,
    "grade_levels": _write_grade_level,
    "classes": _write_class,
}

ACCESS_WRITERS = {
    "students": _write_student_with_access,
    "staff": _write_staff_with_access,
}
