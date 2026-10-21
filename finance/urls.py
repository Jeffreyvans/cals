from django.urls import path

from . import views as v

urlpatterns = [
    path("pay/<int:pk>/", v.record_payment, name="record_payment"),
    path("invoice/<int:pk>/", v.create_invoice, name="create_invoice"),
    path("receipt/<int:pk>/", v.receipt, name="receipt"),
    path("waive/<int:pk>/", v.waive, name="waive"),
    path("credit-note/<int:pk>/", v.credit_note, name="credit_note"),
    path("audit/", v.audit_log, name="audit_log"),
    path("audit/export/", v.export_audit, name="audit_export"),
    path("reports/debtors/", v.report_debtors, name="report_debtors"),
    path("reports/debtors/export/", v.export_debtors, name="export_debtors"),
    path("reports/collections/", v.report_collections, name="report_collections"),
    path("reports/collections/export/", v.export_collections,
         name="export_collections"),
]
