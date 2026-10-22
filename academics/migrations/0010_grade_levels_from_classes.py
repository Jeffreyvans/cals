"""Give every existing class a grade level, so the promotion tool has a way up.

Before this, a class was only a name. Each class now sits on a level of its
own: "Grade 3" becomes a level called "Grade 3" with step 3, and a class
called "Form 2" becomes a level called "Form 2" with step 2.
"""

import re

from django.db import migrations

STEP = re.compile(r"(\d+)\s*$")


def step_of(name):
    """The number at the end of a name, or 0 when there is none."""
    match = STEP.search(name or "")
    return int(match.group(1)) if match else 0


def create_grade_levels(apps, schema_editor):
    SchoolClass = apps.get_model("academics", "SchoolClass")
    GradeLevel = apps.get_model("academics", "GradeLevel")

    for school_class in SchoolClass.objects.all().order_by("institution_id", "level", "name"):
        level, _created = GradeLevel.objects.get_or_create(
            institution_id=school_class.institution_id,
            name=school_class.name,
            defaults={"kind": school_class.level, "order": step_of(school_class.name)},
        )
        if school_class.grade_level_id != level.pk:
            school_class.grade_level = level
            school_class.save(update_fields=["grade_level"])


def remove_grade_levels(apps, schema_editor):
    SchoolClass = apps.get_model("academics", "SchoolClass")
    GradeLevel = apps.get_model("academics", "GradeLevel")

    SchoolClass.objects.update(grade_level=None)
    GradeLevel.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("academics", "0009_structure_grades"),
    ]

    operations = [
        migrations.RunPython(create_grade_levels, remove_grade_levels),
    ]
