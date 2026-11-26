from django.urls import path

from enterprise import views

app_name = "enterprise"

urlpatterns = [
    # Platform administration of organisations.
    path("platform/groups/", views.organisation_list, name="organisation_list"),
    path("platform/groups/new/", views.organisation_create, name="organisation_create"),
    path("platform/groups/<int:pk>/", views.organisation_detail, name="organisation_detail"),
    # Group sign-in and dashboards.
    path("g/<slug:slug>/", views.group_login, name="group_login"),
    path("g/<slug:slug>/dashboard/", views.group_dashboard, name="group_dashboard"),
    path("g/<slug:slug>/switch/", views.group_switch_campus, name="group_switch_campus"),
    path("g/<slug:slug>/students/", views.group_students, name="group_students"),
    path("g/<slug:slug>/request-access/", views.group_request_access,
         name="group_request_access"),
    path("g/<slug:slug>/settings/", views.group_settings, name="group_settings"),
    path("g/<slug:slug>/settings/save/", views.group_setting_save, name="group_setting_save"),
    path("g/<slug:slug>/campus/<int:campus_id>/settings/",
         views.campus_settings, name="campus_settings"),
    path("g/<slug:slug>/campus/<int:campus_id>/settings/save/",
         views.campus_setting_save, name="campus_setting_save"),
    path("g/<slug:slug>/billing/", views.group_billing, name="group_billing"),
    path("g/<slug:slug>/export/", views.group_export, name="group_export"),
    # Platform administration of sponsor portal accounts.
    path("platform/sponsors/", views.sponsor_account_list, name="sponsor_account_list"),
    path("platform/sponsors/new/", views.sponsor_account_create,
         name="sponsor_account_create"),
    path("platform/sponsors/<int:pk>/", views.sponsor_account_detail,
         name="sponsor_account_detail"),
    path("platform/sponsors/report/", views.sponsor_donor_report,
         name="sponsor_donor_report"),
    # Sponsor portal.
    path("sponsor/", views.sponsor_login, name="sponsor_login"),
    path("sponsor/select/", views.sponsor_select, name="sponsor_select"),
    path("sponsor/verify/", views.sponsor_verify, name="sponsor_verify"),
    path("sponsor/logout/", views.sponsor_logout, name="sponsor_logout"),
    path("sponsor/dashboard/", views.sponsor_dashboard, name="sponsor_dashboard"),
    path("sponsor/invoices/", views.sponsor_invoices, name="sponsor_invoices"),
    path("sponsor/statement/", views.sponsor_statement_view, name="sponsor_statement"),
    path("sponsor/receipts/", views.sponsor_receipts_view, name="sponsor_receipts"),
    path("sponsor/receipts/<int:pk>/pdf/", views.sponsor_receipt_pdf,
         name="sponsor_receipt_pdf"),
    path("sponsor/export/", views.sponsor_export, name="sponsor_export"),
    path("sponsor/pay/", views.sponsor_pay, name="sponsor_pay"),
    path("sponsor/credit-note/", views.sponsor_credit_note, name="sponsor_credit_note"),
    path("sponsor/settings/", views.sponsor_settings, name="sponsor_settings"),
    path("sponsor/security/", views.sponsor_security, name="sponsor_security"),
]
