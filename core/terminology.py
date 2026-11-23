"""Institution type drives the words used across the screens.

A primary school says "Pupil" and "Grade 3"; a college says "Student" and
"Programme". Views read these labels instead of hard-coding them.
"""

from django.db import models

DEFAULTS = {
    "primary": {
        "class_label": "Class",
        "grade_label": "Grade",
        "pupil_label": "Pupil",
        "teacher_label": "Teacher",
        "programme_label": "Stream",
        "unit_label": "School",
        "term_label": "Term",
    },
    "secondary": {
        "class_label": "Form",
        "grade_label": "Grade",
        "pupil_label": "Learner",
        "teacher_label": "Teacher",
        "programme_label": "Programme",
        "unit_label": "School",
        "term_label": "Term",
    },
    "college": {
        "class_label": "Class",
        "grade_label": "Year",
        "pupil_label": "Student",
        "teacher_label": "Lecturer",
        "programme_label": "Programme",
        "unit_label": "Faculty",
        "term_label": "Semester",
    },
    "university": {
        "class_label": "Class",
        "grade_label": "Year",
        "pupil_label": "Student",
        "teacher_label": "Lecturer",
        "programme_label": "Programme",
        "unit_label": "Faculty",
        "term_label": "Semester",
    },
    "vocational": {
        "class_label": "Class",
        "grade_label": "Year",
        "pupil_label": "Student",
        "teacher_label": "Instructor",
        "programme_label": "Programme",
        "unit_label": "School",
        "term_label": "Term",
    },
}

FIELDS = (
    ("class_label", "Class label"),
    ("grade_label", "Grade label"),
    ("pupil_label", "Learner label"),
    ("teacher_label", "Teacher label"),
    ("programme_label", "Programme label"),
    ("unit_label", "School / Faculty label"),
    ("term_label", "Term / Semester label"),
)

UNIT_LABELS = ("School", "Faculty", "Division")
TERM_LABELS = ("Term", "Semester")


def default_labels(institution_type):
    """The labels an institution of this type starts with."""
    return dict(DEFAULTS.get(institution_type, DEFAULTS["college"]))


def terminology_for(institution):
    """Return the label dictionary for this institution."""
    if institution is None:
        return dict(DEFAULTS["college"])
    from core.models import Terminology

    record = Terminology.objects.filter(institution=institution).first()
    if record:
        return {
            "class_label": record.class_label,
            "grade_label": record.grade_label,
            "pupil_label": record.pupil_label,
            "teacher_label": record.teacher_label,
            "programme_label": record.programme_label,
            "unit_label": record.unit_label,
            "term_label": record.term_label,
        }
    return default_labels(institution.type)


def terminology_for_current():
    from core.models import Institution
    from core.tenancy import current_institution_id

    institution = Institution.objects.filter(pk=current_institution_id()).first()
    return terminology_for(institution)


def save_terminology(institution, labels):
    """Write the labels for this institution, keeping any it did not supply."""
    from core.models import Terminology

    record, _ = Terminology.objects.get_or_create(institution=institution)
    changed = []
    for field, _label in FIELDS:
        value = (labels.get(field) or "").strip()[:30]
        if value and value != getattr(record, field):
            setattr(record, field, value)
            changed.append(field)
    if changed:
        record.save()
    return record, changed


class TerminologyLabels(models.TextChoices):
    """The label set an institution may pick from when it does not want to type."""

    STANDARD = "standard", "Standard for this institution type"
    INTERNATIONAL = "international", "International (Class / Year / Student / Teacher)"
    LOCAL = "local", "Local wording (edit each word)"


def preset_labels(preset, institution_type="college"):
    """The label set behind a preset on the settings screen."""
    if preset == TerminologyLabels.INTERNATIONAL:
        return {
            "class_label": "Class",
            "grade_label": "Year",
            "pupil_label": "Student",
            "teacher_label": "Teacher",
            "programme_label": "Programme",
            "unit_label": "School",
            "term_label": "Term",
        }
    if preset == TerminologyLabels.LOCAL:
        return {field: "" for field, _label in FIELDS}
    return default_labels(institution_type)
