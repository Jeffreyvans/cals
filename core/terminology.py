"""Institution type drives the words used across the screens.

A primary school says "Pupil" and "Grade 3"; a college says "Student" and
"Programme". Views read these labels instead of hard-coding them.
"""

DEFAULTS = {
    "primary": {
        "class_label": "Class",
        "grade_label": "Grade",
        "pupil_label": "Pupil",
        "teacher_label": "Teacher",
        "programme_label": "Stream",
    },
    "secondary": {
        "class_label": "Form",
        "grade_label": "Grade",
        "pupil_label": "Learner",
        "teacher_label": "Teacher",
        "programme_label": "Programme",
    },
    "college": {
        "class_label": "Class",
        "grade_label": "Year",
        "pupil_label": "Student",
        "teacher_label": "Lecturer",
        "programme_label": "Programme",
    },
    "vocational": {
        "class_label": "Class",
        "grade_label": "Year",
        "pupil_label": "Student",
        "teacher_label": "Instructor",
        "programme_label": "Programme",
    },
}


def terminology_for(institution):
    """Return the label dictionary for this institution."""
    if institution is None:
        return DEFAULTS["college"]
    from core.models import Terminology

    record = Terminology.objects.filter(institution=institution).first()
    if record:
        return {
            "class_label": record.class_label,
            "grade_label": record.grade_label,
            "pupil_label": record.pupil_label,
            "teacher_label": record.teacher_label,
            "programme_label": record.programme_label,
        }
    return DEFAULTS.get(institution.type, DEFAULTS["college"])


def terminology_for_current():
    from core.models import Institution
    from core.tenancy import current_institution_id

    institution = Institution.objects.filter(pk=current_institution_id()).first()
    return terminology_for(institution)
