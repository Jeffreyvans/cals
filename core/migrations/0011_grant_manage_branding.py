"""Give existing institution admins the new branding permission.

``admin.manage_branding`` was added after the default roles were first seeded,
so institution admin roles already in the database do not hold it yet. This
migration grants it to every institution admin. The reverse is a no-op because
the permission is defined in code and recreated by ``ensure_roles``.
"""

from django.db import migrations

BRANDING = "admin.manage_branding"


def grant_branding(apps, schema_editor):
    Permission = apps.get_model("core", "Permission")
    Role = apps.get_model("core", "Role")
    RolePermission = apps.get_model("core", "RolePermission")
    permission, _ = Permission.objects.get_or_create(
        code=BRANDING,
        defaults={
            "description": "Change the logo and the institution colours",
            "category": "admin",
        },
    )
    for role in Role.objects.filter(code="institution_admin"):
        RolePermission.objects.get_or_create(role=role, permission=permission)


def noop(apps, schema_editor):
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0010_institution_website_alter_institution_about"),
    ]

    operations = [
        migrations.RunPython(grant_branding, noop),
    ]
