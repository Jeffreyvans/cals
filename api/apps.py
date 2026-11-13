from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "api"
    verbose_name = "REST API and integrations"

    def ready(self):
        from . import schema, signals  # noqa: F401  (registration and receivers)
