# Generated manually for username reservation

from django.db import migrations, models


def backfill_reserved_usernames(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    ReservedUsername = apps.get_model("accounts", "ReservedUsername")
    seen = set()
    for username in User.objects.values_list("username", flat=True):
        key = str(username or "").strip().lower()[:150]
        if not key or key in seen:
            continue
        ReservedUsername.objects.get_or_create(username=key)
        seen.add(key)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0011_signuplink_created_by_platform_admin"),
    ]

    operations = [
        migrations.CreateModel(
            name="ReservedUsername",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("username", models.CharField(max_length=150, unique=True)),
                ("created", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["username"],
            },
        ),
        migrations.RunPython(
            backfill_reserved_usernames, migrations.RunPython.noop
        ),
    ]
