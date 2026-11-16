from django.apps import AppConfig


class PlatformAdminConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "platform_admin"
    verbose_name = "Platform administration"

    def ready(self):
        from . import signals  # noqa: F401

        # A guarded second chance for a fresh deployment. build.sh is the
        # primary path; this never runs in tests, migrations or collectstatic.
        from .startup import ensure_once

        ensure_once()
