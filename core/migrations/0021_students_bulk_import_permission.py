"""Grant the new bulk-student permission to existing registrars and admins.

The permission is part of the catalogue in code, so new institutions receive it
through ``ensure_roles``. This migration adds it to role rows that were created
before the feature existed.
"""

from django.db import migrations

CODE = "students.bulk_import"


def grant(apps, _schema_editor):
    Permission = apps.get_model("core", "Permission")
    Role = apps.get_model("core", "Role")
    RolePermission = apps.get_model("core", "RolePermission")
    permission, _ = Permission.objects.get_or_create(
        code=CODE,
        defaults={
            "description": "Bulk upload students from a spreadsheet",
            "category": "students",
        },
    )
    for role in Role.objects.filter(code__in=("registrar", "institution_admin")):
        RolePermission.objects.get_or_create(role=role, permission=permission)


def noop(_apps, _schema_editor):
    return None


class Migration(migrations.Migration):
    dependencies = [("core", "0020_institution_custom_domain_and_more")]

    operations = [migrations.RunPython(grant, noop)]
