"""Seed the AI permission codes and give them to institution administrators.

Roles are data, so an existing installation needs these rows without a manual
step. Nobody else receives access automatically; each AI feature also needs its
own switch, consent and the platform kill switch before it can be used.
"""

from django.db import migrations

AI_CODES = ("ai.use", "ai.manage", "ai.view_usage")


def seed(apps, _schema_editor):
    from core.permissions import PERMISSIONS

    Permission = apps.get_model("core", "Permission")
    Role = apps.get_model("core", "Role")
    RolePermission = apps.get_model("core", "RolePermission")
    descriptions = {
        code: (description, category)
        for code, description, category in PERMISSIONS
        if code in AI_CODES
    }
    permissions = {}
    for code in AI_CODES:
        description, category = descriptions[code]
        permissions[code], _ = Permission.objects.get_or_create(
            code=code, defaults={"description": description, "category": category}
        )
    for role in Role.objects.filter(code="institution_admin"):
        for permission in permissions.values():
            RolePermission.objects.get_or_create(role=role, permission=permission)


def unseed(apps, _schema_editor):
    Permission = apps.get_model("core", "Permission")
    RolePermission = apps.get_model("core", "RolePermission")
    RolePermission.objects.filter(permission__code__in=AI_CODES).delete()
    Permission.objects.filter(code__in=AI_CODES).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("ai", "0001_initial"),
        ("core", "0026_department_description_department_is_active_and_more"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
