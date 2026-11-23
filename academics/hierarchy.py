"""Configurable academic hierarchy: templates, defaults and shared filters.

A template is a starting point, never a live link. When an institution is set
up, the current platform template is copied into that institution's own rows
and the template version is recorded on the institution. Editing the platform
template later therefore cannot change a school that is already running.
"""

import re
from datetime import date

ACADEMIC_TEMPLATE_VERSION = 1

# The level sets offered by the wizard and the settings page. A school can use
# any of them, rename the rows and add its own.
LEVEL_SETS = {
    "primary": {
        "label": "Primary (ECD A to Grade 7)",
        "kind": "primary",
        "levels": [
            "ECD A", "ECD B", "Grade 1", "Grade 2", "Grade 3",
            "Grade 4", "Grade 5", "Grade 6", "Grade 7",
        ],
    },
    "secondary": {
        "label": "Secondary (Form 1 to Form 6)",
        "kind": "secondary",
        "levels": [
            "Form 1", "Form 2", "Form 3", "Form 4 (Ordinary Level)",
            "Form 5", "Form 6 (Advanced Level)",
        ],
    },
    "secondary_grade_8_13": {
        "label": "Secondary (Grade 8 to Grade 13)",
        "kind": "secondary",
        "levels": [f"Grade {number}" for number in range(8, 14)],
    },
    "combined": {
        "label": "Combined primary and secondary",
        "kind": "primary",
        "levels": [
            "ECD A", "ECD B", "Grade 1", "Grade 2", "Grade 3", "Grade 4",
            "Grade 5", "Grade 6", "Grade 7", "Form 1", "Form 2", "Form 3",
            "Form 4 (Ordinary Level)", "Form 5", "Form 6 (Advanced Level)",
        ],
    },
    "college": {
        "label": "College / university (Year 1 to Year 4)",
        "kind": "college",
        "levels": ["Year 1", "Year 2", "Year 3", "Year 4"],
    },
    "college_long": {
        "label": "Long programmes (Year 1 to Year 6)",
        "kind": "college",
        "levels": ["Year 1", "Year 2", "Year 3", "Year 4", "Year 5", "Year 6"],
    },
}

STREAM_SUGGESTIONS = [
    "A", "B", "C", "D", "Red", "Blue", "Green", "Gold",
    "Science", "Commercial", "Arts",
]

SCHOOL_DEPARTMENTS = {
    "School of Computer Science and Information Technology": [
        "Computer Science", "Information Systems", "Software Engineering",
        "Networking and Cybersecurity",
    ],
    "School of Business Sciences": [
        "Accounting", "Finance", "Marketing", "Management", "Human Resources",
        "Entrepreneurship",
    ],
    "School of Engineering and Technology": [
        "Electrical", "Mechanical", "Civil", "Chemical", "Mechatronics",
    ],
    "School of Education": [
        "Primary Education", "Secondary Education", "Educational Leadership",
    ],
    "School of Health Sciences": [
        "Nursing", "Pharmacy", "Public Health", "Medical Laboratory",
    ],
    "School of Agriculture and Natural Resources": [
        "Crop Science", "Animal Science", "Agricultural Economics",
        "Environmental Science",
    ],
    "School of Humanities and Social Sciences": [
        "Languages", "Psychology", "Sociology", "Development Studies",
        "Media and Communication",
    ],
    "School of Law": [],
    "School of Natural and Applied Sciences": [
        "Mathematics", "Physics", "Chemistry", "Biology", "Statistics",
    ],
    "School of Hospitality and Tourism": [],
    "School of Arts and Design": [],
}

VOCATIONAL_DEPARTMENTS = {
    "School of Computer Science and Information Technology": ["ICT"],
    "School of Business Sciences": ["Business Studies"],
    "School of Engineering and Technology": [
        "Electrical Installation", "Motor Mechanics", "Welding and Fabrication",
        "Building and Construction", "Plumbing", "Carpentry",
    ],
    "School of Hospitality and Tourism": [
        "Hairdressing and Cosmetology", "Fashion and Fabrics",
        "Hospitality and Catering",
    ],
    "School of Agriculture and Natural Resources": ["Agriculture"],
}

SCHOOL_DEPARTMENTS_DATA = [
    "Languages", "Mathematics", "Sciences", "Humanities", "Commercials",
    "Practical Subjects", "Sports and Creative Arts",
]

TERM_SCHEMES = {
    "terms": ["Term 1", "Term 2", "Term 3"],
    "semesters": ["Semester 1", "Semester 2"],
}

DEFAULT_LEVEL_SET = {
    "primary": "primary",
    "secondary": "secondary",
    "college": "college",
    "university": "college",
    "vocational": "college",
}

DEFAULT_TERM_SCHEME = {
    "primary": "terms",
    "secondary": "terms",
    "college": "semesters",
    "university": "semesters",
    "vocational": "terms",
}


def code_for(name):
    """A short stable code for a default name, used to match imports later."""
    words = re.sub(r"[^A-Za-z0-9 ]+", " ", str(name or "")).split()
    words = [word for word in words if word.lower() not in ("school", "of", "and", "the")]
    if not words:
        words = re.sub(r"[^A-Za-z0-9]+", "", str(name or "")).upper()[:4]
        return words or "ITEM"
    code = "".join(word if word.isdigit() else word[0] for word in words).upper()
    if len(code) < 3 and not any(character.isdigit() for character in code):
        code = "".join(words)[:4].upper()
    return code[:20]


def _rows(names, kind=None):
    return [
        {"name": name, "code": code_for(name), "kind": kind or "", "order": index}
        for index, name in enumerate(names, start=1)
    ]


def builtin_template(institution_type, level_set=None, term_scheme=None):
    """The platform's built-in starting point for one institution type."""
    level_set = level_set or DEFAULT_LEVEL_SET.get(institution_type, "college")
    term_scheme = term_scheme or DEFAULT_TERM_SCHEME.get(institution_type, "terms")
    levels = LEVEL_SETS.get(level_set, LEVEL_SETS["college"])
    schools = []
    if institution_type in ("college", "university", "vocational"):
        mapping = VOCATIONAL_DEPARTMENTS if institution_type == "vocational" \
            else SCHOOL_DEPARTMENTS
        for school_name, department_names in mapping.items():
            schools.append({
                "name": school_name,
                "code": code_for(school_name),
                "description": "",
                "departments": [
                    {"name": name, "code": code_for(name)}
                    for name in department_names
                ],
            })
        departments = []
    else:
        departments = [
            {"name": name, "code": code_for(name)} for name in SCHOOL_DEPARTMENTS_DATA
        ]
    return {
        "version": ACADEMIC_TEMPLATE_VERSION,
        "level_set": level_set,
        "term_scheme": term_scheme,
        "levels": _rows(levels["levels"], levels["kind"]),
        "schools": schools,
        "departments": departments,
        "streams": list(STREAM_SUGGESTIONS[:4]),
        "terms": list(TERM_SCHEMES.get(term_scheme, TERM_SCHEMES["terms"])),
    }


def validate_template_data(data):
    """Check and normalise the JSON body of an editable platform template.

    Templates are global data, so a bad edit here can affect every future
    institution. This keeps the shape stable for :func:`apply_template` and
    gives the platform screen a useful error instead of a broken setup wizard.
    """
    if not isinstance(data, dict):
        raise ValueError("A template must be a JSON object.")
    result = dict(data)
    for key in ("levels", "schools", "departments", "streams", "terms"):
        value = result.get(key) or []
        if not isinstance(value, list):
            raise ValueError(f"'{key}' must be a list.")
        result[key] = value
    seen = set()
    levels = []
    for row in result["levels"]:
        if not isinstance(row, dict) or not str(row.get("name") or "").strip():
            raise ValueError("Every level needs a name.")
        code = str(row.get("code") or code_for(row["name"])).strip()[:20]
        if code.lower() in seen:
            raise ValueError(f"Level code '{code}' is used twice.")
        seen.add(code.lower())
        kind = str(row.get("kind") or "").strip()
        if kind and kind not in ("primary", "secondary", "college"):
            raise ValueError(f"Unknown level type '{kind}'.")
        levels.append({
            "name": str(row["name"]).strip()[:60],
            "code": code,
            "kind": kind,
            "order": int(row.get("order") or 0),
        })
    result["levels"] = levels
    seen = set()
    schools = []
    for row in result["schools"]:
        if not isinstance(row, dict) or not str(row.get("name") or "").strip():
            raise ValueError("Every school needs a name.")
        code = str(row.get("code") or code_for(row["name"])).strip()[:20]
        if code.lower() in seen:
            raise ValueError(f"School code '{code}' is used twice.")
        seen.add(code.lower())
        departments = []
        for department in row.get("departments") or []:
            if not isinstance(department, dict) or not str(department.get("name") or "").strip():
                raise ValueError("Every department needs a name.")
            departments.append({
                "name": str(department["name"]).strip()[:120],
                "code": str(department.get("code") or code_for(department["name"])).strip()[:20],
                "description": str(department.get("description") or ""),
            })
        schools.append({
            "name": str(row["name"]).strip()[:120],
            "code": code,
            "description": str(row.get("description") or ""),
            "departments": departments,
        })
    result["schools"] = schools
    seen = set()
    departments = []
    for row in result["departments"]:
        if not isinstance(row, dict) or not str(row.get("name") or "").strip():
            raise ValueError("Every department needs a name.")
        code = str(row.get("code") or code_for(row["name"])).strip()[:20]
        if code.lower() in seen:
            raise ValueError(f"Department code '{code}' is used twice.")
        seen.add(code.lower())
        departments.append({
            "name": str(row["name"]).strip()[:120],
            "code": code,
            "description": str(row.get("description") or ""),
        })
    result["departments"] = departments
    for key in ("streams", "terms"):
        result[key] = [str(value).strip()[:40] for value in result[key] if str(value).strip()]
    level_set = str(result.get("level_set") or "").strip()
    if level_set and level_set not in LEVEL_SETS:
        raise ValueError(f"Unknown level set '{level_set}'.")
    term_scheme = str(result.get("term_scheme") or "").strip()
    if term_scheme and term_scheme not in TERM_SCHEMES:
        raise ValueError(f"Unknown term scheme '{term_scheme}'.")
    result["version"] = result.get("version") or ACADEMIC_TEMPLATE_VERSION
    return result


def template_for(institution_type, level_set=None, term_scheme=None):
    """The latest editable platform template, or the built-in default.

    A template is global platform data, so this is deliberately not tenant
    scoped. Nothing here is ever written into an existing institution.
    """
    from platform_admin.models import AcademicTemplate

    row = AcademicTemplate.objects.filter(code=institution_type, is_active=True) \
        .order_by("-version", "-pk").first()
    data = row.data if row else builtin_template(institution_type)
    data = dict(data)
    data["version"] = row.version if row else ACADEMIC_TEMPLATE_VERSION
    data["template_id"] = row.pk if row else None
    if level_set:
        # A different level set changes the levels but keeps the platform's
        # schools, departments and terminology, which are institution-wide.
        data["levels"] = _rows(LEVEL_SETS[level_set]["levels"], LEVEL_SETS[level_set]["kind"])
        data["level_set"] = level_set
    if term_scheme:
        data["terms"] = list(TERM_SCHEMES[term_scheme])
        data["term_scheme"] = term_scheme
    return data


def _selected(item, selected, group, key):
    if selected is None:
        return True
    choices = (selected or {}).get(group)
    if choices is None:
        return True
    return key in choices


def _override(item, renames, group, key):
    change = ((renames or {}).get(group) or {}).get(key) or {}
    result = dict(item)
    for field in ("name", "code", "description"):
        value = change.get(field)
        if value:
            result[field] = value
    return result


def apply_template(
    institution,
    institution_type,
    *,
    level_set=None,
    term_scheme=None,
    selected=None,
    renames=None,
    additions=None,
    actor=None,
    request=None,
    create_terms=True,
):
    """Copy a platform template into a new institution.

    ``selected`` holds ticked keys, ``renames`` holds edited names/codes and
    ``additions`` holds rows the administrator typed in. Existing rows are
    matched by code then name and updated, never deleted, so the function is
    safe to rerun. The template version is stored on the institution at the
    time of the copy and never used again to change these rows.
    """
    from django.db import transaction

    from academics.models import AcademicSchool, GradeLevel, Term
    from core.audit import log_action

    data = template_for(institution_type, level_set=level_set, term_scheme=term_scheme)
    additions = additions or {}
    created = {"schools": 0, "departments": 0, "levels": 0, "terms": 0}
    with transaction.atomic():
        school_map = {}
        for index, raw in enumerate(data.get("schools") or [], start=1):
            if not _selected(raw, selected, "schools", raw["code"]):
                continue
            row = _override(raw, renames, "schools", raw["code"])
            school, school_created = AcademicSchool.all_objects.get_or_create(
                institution=institution, code=row["code"][:20],
                defaults={
                    "name": row["name"][:120],
                    "description": row.get("description", ""),
                    "sort_order": index,
                },
            )
            school_map[row["code"]] = school
            created["schools"] += int(school_created)
            for department_index, department_raw in enumerate(row.get("departments") or [], start=1):
                if not _selected(department_raw, selected, "departments",
                                 department_raw["code"]):
                    continue
                department_row = _override(department_raw, renames, "departments",
                                           department_raw["code"])
                _get_or_create_department(
                    institution, department_row, school=school,
                    sort_order=department_index, created=created)
        for index, raw in enumerate(data.get("departments") or [], start=1):
            if not _selected(raw, selected, "departments", raw["code"]):
                continue
            row = _override(raw, renames, "departments", raw["code"])
            _get_or_create_department(institution, row, sort_order=index, created=created)

        for index, raw in enumerate(data.get("levels") or [], start=1):
            if not _selected(raw, selected, "levels", raw["code"]):
                continue
            row = _override(raw, renames, "levels", raw["code"])
            level, level_created = GradeLevel.all_objects.get_or_create(
                institution=institution, code=row["code"][:20],
                defaults={
                    "name": row["name"][:60],
                    "kind": row.get("kind") or GradeLevel.Kind.SECONDARY,
                    "order": int(row.get("order") or index),
                },
            )
            created["levels"] += int(level_created)

        for extra in additions.get("schools") or []:
            if extra.get("name"):
                school, school_created = AcademicSchool.all_objects.get_or_create(
                    institution=institution,
                    code=(extra.get("code") or code_for(extra["name"]))[:20],
                    defaults={"name": extra["name"][:120],
                              "description": extra.get("description", "")})
                created["schools"] += int(school_created)
        for extra in additions.get("departments") or []:
            if extra.get("name"):
                school = school_map.get(extra.get("school") or "")
                _get_or_create_department(institution, extra, school=school, created=created)
        for extra in additions.get("levels") or []:
            if extra.get("name"):
                level, level_created = GradeLevel.all_objects.get_or_create(
                    institution=institution,
                    code=(extra.get("code") or code_for(extra["name"]))[:20],
                    defaults={"name": extra["name"][:60],
                              "kind": extra.get("kind") or GradeLevel.Kind.SECONDARY,
                              "order": int(extra.get("order") or 99)},
                )
                created["levels"] += int(level_created)

        year = None
        from academics.models import AcademicYear

        year = AcademicYear.all_objects.filter(
            institution=institution, is_current=True).first()
        if year and create_terms:
            for term_name in data.get("terms") or []:
                _term, term_created = Term.all_objects.get_or_create(
                    institution=institution, academic_year=year, name=term_name[:40],
                    defaults={
                        "start_date": year.start_date,
                        "end_date": year.end_date,
                    },
                )
                created["terms"] += int(term_created)

        settings = dict(institution.settings or {})
        settings["academic_template"] = {
            "version": data["version"],
            "template_id": data.get("template_id"),
            "level_set": data.get("level_set"),
            "term_scheme": data.get("term_scheme"),
            "applied_on": date.today().isoformat(),
        }
        institution.settings = settings
        institution.save(update_fields=["settings"])

    log_action(
        actor, "academic.template_applied", institution, institution=institution,
        after={"version": data["version"], "created": created}, request=request)
    return created


def _get_or_create_department(institution, row, school=None, sort_order=0, created=None):
    from core.models import Department

    department, was_created = Department.all_objects.get_or_create(
        institution=institution, code=row["code"][:20],
        defaults={
            "name": row["name"][:120],
            "description": row.get("description", ""),
            "school": school,
            "sort_order": int(row.get("sort_order") or sort_order),
        },
    )
    if created is not None:
        created["departments"] += int(was_created)
    return department


def hierarchy_parts(obj):
    """The (school, department, level, class) chain for a related record.

    Records differ: a student has a programme and a class, a teacher has a
    staff profile, a course has a programme. Missing links are simply empty so
    every caller can use the same labels and filters.
    """
    school = getattr(obj, "school", None)
    department = getattr(obj, "department", None)
    level = getattr(obj, "grade_level", None) or getattr(obj, "level", None)
    school_class = getattr(obj, "school_class", None)
    programme = getattr(obj, "programme", None)
    if programme is not None and hasattr(programme, "programme"):
        programme = programme.programme
    if programme is not None:
        school = school or getattr(programme, "school", None)
        department = department or getattr(programme, "department", None)
    if school_class is not None:
        level = level or getattr(school_class, "grade_level", None)
        school = school or getattr(school_class, "school", None)
        department = department or getattr(school_class, "department", None)
    return school, department, level, school_class


def hierarchy_label(obj):
    """A human-readable chain, for example 'Faculty of Science, Department of Physics'."""
    school, department, _level, _school_class = hierarchy_parts(obj)
    parts = []
    if school is not None:
        parts.append(str(school))
    if department is not None:
        parts.append(f"Department of {department.name}")
    return ", ".join(parts)


def hierarchy_context(obj):
    """Small dictionary shared by templates, PDFs and API responses."""
    school, department, level, school_class = hierarchy_parts(obj)
    return {
        "school": school,
        "department": department,
        "level": level,
        "school_class": school_class,
        "label": hierarchy_label(obj),
    }


def filter_hierarchy(queryset, request, *, school_field=None, department_field=None,
                     level_field=None, class_field=None):
    """Apply the shared GET filters when they are present.

    The caller names the fields on its own model, so the same four query keys
    work for students, timetable entries, enrolments and fee records. A filter
    that a screen does not support is simply ignored.
    """
    get = getattr(request, "GET", {})
    school = (get.get("school") or "").strip()
    department = (get.get("department") or "").strip()
    level = (get.get("level") or "").strip()
    school_class = (get.get("school_class") or get.get("class") or "").strip()
    if school and school.isdigit() and school_field:
        queryset = queryset.filter(**{f"{school_field}_id": school})
    if department and department.isdigit() and department_field:
        queryset = queryset.filter(**{f"{department_field}_id": department})
    if level and level.isdigit() and level_field:
        queryset = queryset.filter(**{f"{level_field}_id": level})
    if school_class and school_class.isdigit() and class_field:
        queryset = queryset.filter(**{f"{class_field}_id": school_class})
    return queryset


def has_hierarchy_filters(request):
    """True when any of the shared hierarchy filters was supplied."""
    get = getattr(request, "GET", {})
    return any(str(get.get(key, "")).strip().isdigit()
               for key in ("school", "department", "level", "school_class", "class"))


def hierarchy_filter_context(request):
    """Choice lists for the shared filter form."""
    from academics.models import GradeLevel, SchoolClass
    from core.models import Department

    from .models import AcademicSchool

    get = getattr(request, "GET", {})
    institution = getattr(request, "institution", None)

    def scoped(model):
        rows = model.objects.all()
        return rows.filter(institution=institution) if institution is not None else rows

    hierarchy_keys = {"school", "department", "level", "school_class", "class", "page"}
    pairs = get.lists() if hasattr(get, "lists") else get.items()
    other_params = [
        (key, value)
        for key, values in pairs
        for value in (values if isinstance(values, (list, tuple)) else [values])
        if key not in hierarchy_keys
    ]
    return {
        "schools": scoped(AcademicSchool),
        "departments": scoped(Department),
        "levels": scoped(GradeLevel),
        "classes": scoped(SchoolClass),
        "other_params": other_params,
        "hierarchy_filters": {
            "school": get.get("school", ""),
            "department": get.get("department", ""),
            "level": get.get("level", ""),
            "class": get.get("school_class", get.get("class", "")),
        },
    }


def breadcrumb_label(request):
    """The current filters, shown under the institution in the app breadcrumb."""
    context = hierarchy_filter_context(request)
    chosen = context["hierarchy_filters"]
    names = []
    for key, rows in (("school", context["schools"]),
                      ("department", context["departments"]),
                      ("level", context["levels"]),
                      ("class", context["classes"])):
        value = chosen[key]
        if not value.isdigit():
            continue
        match = next((row for row in rows if str(row.pk) == value), None)
        if match is not None:
            names.append(str(match))
    return " / ".join(names)
