"""Give every enrolment a real term instead of the name of one.

The old column held text such as "Term 1". A new column points at the term
itself and this migration moves the names across, so the history of an
institution is never lost.
"""

from django.db import migrations, models
import django.db.models.deletion


def copy_terms(apps, schema_editor):
    """Move the term name on every enrolment onto the term it names."""
    Enrollment = apps.get_model("academics", "Enrollment")
    Term = apps.get_model("academics", "Term")

    by_institution = {}
    for term in Term._base_manager.all():
        by_institution.setdefault(term.institution_id, []).append(term)

    for enrollment in Enrollment._base_manager.all().order_by("pk"):
        if enrollment.term_ref_id:
            continue
        options = by_institution.get(enrollment.institution_id, [])
        label = (enrollment.term or "").strip()
        match = next((t for t in options if t.name == label and t.is_current), None)
        if match is None:
            match = next((t for t in options if t.name == label), None)
        if match is None:
            match = next((t for t in options if t.is_current), None)
        if match is not None:
            enrollment.term_ref_id = match.pk
            enrollment.save(update_fields=["term_ref"])


def clear_terms(apps, schema_editor):
    """Nothing to undo: the old name column is dropped by the next migration."""


class Migration(migrations.Migration):

    dependencies = [
        ("academics", "0010_grade_levels_from_classes"),
    ]

    operations = [
        migrations.AddField(
            model_name="enrollment",
            name="term_ref",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="enrollments", to="academics.term"),
        ),
        migrations.RunPython(copy_terms, clear_terms),
    ]
