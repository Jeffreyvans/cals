from django.apps import AppConfig


class FiscalisationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "fiscalisation"
    verbose_name = "Fiscalisation (ZIMRA FDMS)"

    def ready(self):
        # Connect to the finance signal here so the two apps stay decoupled.
        from finance.signals import financial_document_issued

        from . import services

        financial_document_issued.connect(
            services.on_financial_document_issued,
            dispatch_uid="fiscalisation.issue",
        )
