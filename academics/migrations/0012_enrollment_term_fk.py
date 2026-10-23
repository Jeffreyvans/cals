"""Put the real term in place of the name of one."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academics", "0011_enrollment_term_ref"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="enrollment", name="uniq_enrol_course"),
        migrations.RemoveConstraint(
            model_name="enrollment", name="uniq_enrol_subject"),
        migrations.RemoveField(
            model_name="enrollment", name="term"),
        migrations.RenameField(
            model_name="enrollment", old_name="term_ref", new_name="term"),
        migrations.AddConstraint(
            model_name="enrollment",
            constraint=models.UniqueConstraint(
                condition=models.Q(("course__isnull", False)),
                fields=("student", "course", "term"), name="uniq_enrol_course")),
        migrations.AddConstraint(
            model_name="enrollment",
            constraint=models.UniqueConstraint(
                condition=models.Q(("subject__isnull", False)),
                fields=("student", "subject", "term"), name="uniq_enrol_subject")),
    ]
