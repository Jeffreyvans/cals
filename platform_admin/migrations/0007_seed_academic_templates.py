"""Seed the versioned platform starting points for each institution type."""

import hashlib
import json

from django.db import migrations

TYPES = ("primary", "secondary", "college", "university", "vocational")


def seed(apps, schema_editor):
    from academics.hierarchy import builtin_template

    AcademicTemplate = apps.get_model("platform_admin", "AcademicTemplate")
    for code in TYPES:
        data = builtin_template(code)
        AcademicTemplate.objects.update_or_create(
            code=code, version=1,
            defaults={
                "name": f"Standard {code} hierarchy",
                "data": data,
                "body_hash": hashlib.sha256(
                    json.dumps(data, sort_keys=True, default=str).encode("utf-8")
                ).hexdigest(),
                "is_active": True,
            },
        )


def unseed(apps, schema_editor):
    apps.get_model("platform_admin", "AcademicTemplate").objects.filter(version=1).delete()


class Migration(migrations.Migration):
    dependencies = [("platform_admin", "0006_alter_institutionapplication_institution_type_and_more")]

    operations = [migrations.RunPython(seed, unseed)]
