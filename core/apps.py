from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Core"

    def ready(self):
        from . import (
            cache_signals,
            checks,  # noqa: F401  (registers the system checks)
            db_monitor,
        )

        cache_signals.connect()
        db_monitor.connect()
