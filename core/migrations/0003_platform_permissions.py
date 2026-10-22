"""Keep platform permissions out of the roles inside a school.

`admin.create_institutions` belongs to the platform, not to a school. The
default institution's roles were seeded before that rule existed, so this
migration takes the permission away again.
"""

from django.db import migrations


def remove_platform_permissions(apps, schema_editor):
    from core.roles import PLATFORM_ONLY

    Permission = apps.get_model("core", "Permission")
    RolePermission = apps.get_model("core", "RolePermission")
    codes = list(Permission.objects.filter(code__in=PLATFORM_ONLY).values_list("code", flat=True))
    if not codes:
        return
    RolePermission.objects.filter(permission__code__in=codes).delete()


def noop(apps, schema_editor):
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_default_institution"),
    ]

    operations = [
        migrations.RunPython(remove_platform_permissions, noop),
    ]
