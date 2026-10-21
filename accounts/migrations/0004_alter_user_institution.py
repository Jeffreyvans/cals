"""Every user now belongs to an institution.

The rows were given the default institution by ``core.0002_default_institution``,
so this can safely tighten the column.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_user_extra_roles_user_institution_user_primary_role"),
        ("core", "0002_default_institution"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="institution",
            field=models.ForeignKey(
                blank=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="users",
                to="core.institution",
            ),
        ),
    ]
