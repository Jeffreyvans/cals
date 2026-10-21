"""Every finance row now belongs to an institution.

The rows were given the default institution by ``core.0002_default_institution``,
so this can safely tighten the column.
"""

import django.db.models.deletion
from django.db import migrations, models

MODELS = ["invoice", "moneylog", "payment", "waiver"]


class Migration(migrations.Migration):
    dependencies = [
        ("finance", "0007_invoice_institution_moneylog_institution_and_more"),
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
