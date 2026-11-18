"""URLs for public applications, the platform and institution consent."""

from django.urls import path

from . import views

app_name = "platform_admin"

urlpatterns = [
    # Public application flow.
    path("apply/", views.apply, name="apply"),
    path("apply/status/<uuid:token>/", views.apply_status, name="apply_status"),
    path("apply/status/<uuid:token>/reply/", views.apply_reply, name="apply_reply"),
    path("apply/status/<uuid:token>/withdraw/", views.apply_withdraw, name="apply_withdraw"),
    path("platform/privacy/", views.platform_privacy, name="privacy"),
    path("platform/data-processing/", views.data_processing_statement,
         name="data_processing"),
    # Platform dashboard, inbox and applications.
    path("platform/", views.dashboard, name="dashboard"),
    path("platform/inbox/", views.inbox, name="inbox"),
    path("platform/applications/", views.applications, name="applications"),
    path("platform/applications/<int:pk>/", views.application_detail,
         name="application_detail"),
    path("platform/applications/<int:pk>/documents/<int:document_pk>/",
         views.application_document, name="application_document"),
    path("platform/requests/<int:pk>/", views.platform_request_detail,
         name="platform_request_detail"),
    # Institutions.
    path("platform/institutions/", views.institutions, name="institutions"),
    path("platform/institutions/new/", views.institution_create,
         name="institution_create"),
    path("platform/institutions/<int:pk>/", views.institution_detail,
         name="institution_detail"),
    path("platform/institutions/<int:pk>/modules/", views.institution_modules,
         name="institution_modules"),
    path("platform/institutions/<int:pk>/admins/", views.institution_admins,
         name="institution_admins"),
    path("platform/institutions/<int:pk>/admins/new/", views.onboarding_admin_create,
         name="onboarding_admin_create"),
    path("platform/institutions/<int:pk>/admins/<int:user_pk>/credentials/",
         views.onboarding_credential, name="onboarding_credential"),
    # Plans, subscriptions, invoices and payments.
    path("platform/plans/", views.plans, name="plans"),
    path("platform/plans/<int:pk>/", views.plan_detail, name="plan_detail"),
    path("platform/subscriptions/", views.subscriptions, name="subscriptions"),
    path("platform/invoices/", views.invoices, name="invoices"),
    path("platform/invoices/<int:pk>/", views.invoice_detail, name="invoice_detail"),
    path("platform/payments/", views.payments, name="payments"),
    # Staff, roles and operations.
    path("platform/staff/", views.staff_list, name="staff"),
    path("platform/staff/<int:pk>/", views.staff_detail, name="staff_detail"),
    path("platform/roles/", views.roles, name="roles"),
    path("platform/roles/<int:pk>/", views.role_detail, name="role_detail"),
    path("platform/health/", views.health, name="health"),
    path("platform/usage/", views.usage, name="usage"),
    path("platform/errors/", views.errors, name="errors"),
    path("platform/email-health/", views.email_health, name="email_health"),
    path("platform/email-health/<int:pk>/resend/", views.email_health,
         name="email_resend"),
    path("platform/backups/", views.backups, name="backups"),
    path("platform/audit/", views.audit_log, name="audit_log"),
    path("platform/audit/export/", views.audit_export, name="audit_export"),
    path("platform/announcements/", views.announcements, name="announcements"),
    path("platform/settings/", views.settings_view, name="settings"),
    # Consent requests and grants (platform side).
    path("platform/access/", views.access_requests, name="access_requests"),
    path("platform/access/new/", views.access_request_create, name="access_request_create"),
    path("platform/access/emergency/", views.emergency_access_create,
         name="emergency_access_create"),
    path("platform/access/<int:pk>/", views.access_request_detail,
         name="access_request_detail"),
    path("platform/grants/", views.grants, name="grants"),
    # Two-factor and fresh password.
    path("platform/security/", views.security, name="security"),
    path("platform/reauth/", views.reauth, name="reauth"),
    # Institution administrator consent screens.
    path("platform/consent/", views.institution_access_requests,
         name="institution_access_requests"),
    path("platform/consent/<int:pk>/", views.institution_access_request_detail,
         name="institution_access_request_detail"),
    path("platform/consent/<int:pk>/decide/", views.institution_access_decide,
         name="institution_access_decide"),
    path("platform/consent/grants/<int:pk>/revoke/", views.institution_grant_revoke,
         name="institution_grant_revoke"),
    path("platform/consent/data-access/", views.institution_data_access,
         name="institution_data_access"),
    path("platform/consent/data-access/export/", views.institution_data_access_export,
         name="institution_data_access_export"),
    path("platform/consent/log/", views.institution_consent_log,
         name="institution_consent_log"),
    path("platform/consent/log/accept/", views.institution_accept_data_processing,
         name="institution_accept_data_processing"),
]
