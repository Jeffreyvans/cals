"""Every academic row now belongs to an institution.

The rows were given the default institution by ``core.0002_default_institution``,
so this can safely tighten the column.
"""

import django.db.models.deletion
from django.db import migrations, models

MODELS = [
    "academicyear",
    "activity",
    "announcement",
    "attendance",
    "classteacher",
    "course",
    "enrollment",
    "examsitting",
    "gradeband",
    "guardian",
    "programme",
    "schoolclass",
    "schoolsettings",
    "stream",
    "student",
    "subject",
    "term",
    "timetableentry",
]


class Migration(migrations.Migration):
    dependencies = [
        ("academics", "0007_academicyear_institution_activity_institution_and_more"),
        ("core", "0002_default_institution"),
    ]

    operations = [
        migrations.AlterField(
            model_name=model_name,
            name="institution",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="%(class)s_records",
                to="core.institution",
            ),
        )
        for model_name in MODELS
    ]
