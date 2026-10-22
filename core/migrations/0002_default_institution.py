"""Create the default institution and give every existing row to it.

Existing data belongs to the single school the installation started as. The
default institution is created here so a fresh database always has one.
"""

from django.db import migrations

DEFAULT_NAME = "Lindenet Institute of Technology"
DEFAULT_SLUG = "default"


def create_default_institution(apps, schema_editor):
    Institution = apps.get_model("core", "Institution")
    InstitutionModule = apps.get_model("core", "InstitutionModule")
    inst, _ = Institution.objects.get_or_create(
        slug=DEFAULT_SLUG,
        defaults={"name": DEFAULT_NAME, "type": "vocational", "currency": "$"},
    )
    from core.tenant_defaults import DEFAULT_MODULES

    for code in DEFAULT_MODULES.get(inst.type, []):
        InstitutionModule.objects.get_or_create(
            institution=inst, code=code, defaults={"enabled": True}
        )


def backfill_institutions(apps, schema_editor):
    Institution = apps.get_model("core", "Institution")
    inst = Institution.objects.filter(slug=DEFAULT_SLUG).first()
    if inst is None:
        inst = Institution.objects.create(
            slug=DEFAULT_SLUG, name=DEFAULT_NAME, type="vocational", currency="$"
        )
    for label in ("academics", "finance", "accounts", "core"):
        try:
            models = apps.get_app_config(label).get_models()
        except LookupError:
            continue
        for model in models:
            if not hasattr(model, "institution"):
                continue
            model.objects.filter(institution__isnull=True).update(institution=inst)


def seed_roles(apps, schema_editor):
    """Give the default institution its default roles and permissions."""
    from core.permissions import PERMISSIONS
    from core.roles import DEFAULT_ROLES, PLATFORM_ONLY

    Institution = apps.get_model("core", "Institution")
    Permission = apps.get_model("core", "Permission")
    Role = apps.get_model("core", "Role")
    RolePermission = apps.get_model("core", "RolePermission")
    inst = Institution.objects.filter(slug=DEFAULT_SLUG).first()
    if inst is None:
        return
    permissions = {}
    for code, description, category in PERMISSIONS:
        permission, _ = Permission.objects.get_or_create(
            code=code, defaults={"description": description, "category": category}
        )
        permissions[code] = permission
    for spec in DEFAULT_ROLES:
        role, _ = Role.objects.get_or_create(
            institution=inst,
            code=spec["code"],
            defaults={
                "name": spec["name"],
                "is_system": True,
                "description": spec["description"],
            },
        )
        codes = (
            [c for c in permissions if c not in PLATFORM_ONLY]
            if spec["permissions"] == "*"
            else [c for c in spec["permissions"] if c in permissions]
        )
        for code in codes:
            RolePermission.objects.get_or_create(role=role, permission=permissions[code])


def noop(apps, schema_editor):
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_initial"),
        ("accounts", "0003_user_extra_roles_user_institution_user_primary_role"),
        ("academics", "0007_academicyear_institution_activity_institution_and_more"),
        ("finance", "0007_invoice_institution_moneylog_institution_and_more"),
    ]

    operations = [
        migrations.RunPython(create_default_institution, noop),
        migrations.RunPython(backfill_institutions, noop),
        migrations.RunPython(seed_roles, noop),
    ]
