from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Core"

    def ready(self):
        from django.contrib.auth.signals import user_logged_in

        from . import (
            cache_signals,
            checks,  # noqa: F401  (registers the system checks)
            db_monitor,
            session_signals,
        )

        cache_signals.connect()
        db_monitor.connect()
        user_logged_in.connect(
            session_signals.bind_session_host,
            dispatch_uid="core-bind-session-host",
        )
