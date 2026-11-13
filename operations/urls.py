from django.urls import path

from . import views as v

app_name = "operations"

urlpatterns = [
    path("", v.hub, name="hub"),
    path("hr/leave/<int:pk>/decision/", v.leave_decision, name="leave_decision"),
    path("payroll/runs/<int:pk>/", v.payroll_run_detail, name="payroll_run_detail"),
    path("payroll/runs/<int:pk>/generate/", v.payroll_run_generate, name="payroll_run_generate"),
    path("payroll/runs/<int:pk>/approve/", v.payroll_run_approve, name="payroll_run_approve"),
    path("payroll/runs/<int:pk>/post/", v.payroll_run_post, name="payroll_run_post"),
    path("payroll/runs/<int:pk>/bank.csv", v.payroll_run_bank, name="payroll_run_bank"),
    path(
        "payroll/payslips/<int:pk>/pdf/",
        v.payroll_payslip_pdf,
        name="payroll_payslip_pdf",
    ),
    path(
        "compliance/policies/<int:pk>/acknowledge/", v.policy_acknowledge, name="policy_acknowledge"
    ),
    path("marketing/newsletters/<int:pk>/send/", v.newsletter_send, name="newsletter_send"),
    path(
        "procurement/requisitions/<int:pk>/",
        v.requisition_detail,
        name="requisition_detail",
    ),
    path(
        "procurement/quotations/<int:pk>/award/",
        v.quotation_award,
        name="quotation_award",
    ),
    path(
        "procurement/supplier-invoices/<int:pk>/match/",
        v.supplier_invoice_match,
        name="supplier_invoice_match",
    ),
    path("<slug:module_code>/", v.module_hub, name="module"),
    path("<slug:module_code>/<slug:kind_slug>/", v.record_list, name="list"),
    path("<slug:module_code>/<slug:kind_slug>/new/", v.record_create, name="create"),
    path("<slug:module_code>/<slug:kind_slug>/<int:pk>/edit/", v.record_edit, name="edit"),
    path("<slug:module_code>/<slug:kind_slug>/<int:pk>/delete/", v.record_delete, name="delete"),
]
