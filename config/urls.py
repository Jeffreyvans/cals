from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from core.error_views import (
    bad_request,
    error_catalogue_js,
    not_found,
    permission_denied,
    server_error,
)
from core.public_views import manifest
from core.views import billing_contact, offline, service_worker, system_status
from core.webhooks import mailjet_webhook, resend_webhook

from .health import health_check

handler400 = bad_request
handler403 = permission_denied
handler404 = not_found
handler500 = server_error

urlpatterns = [
    path("health/", health_check, name="health_check"),
    path("status/", system_status, name="system_status"),
    path("errors/catalogue.js", error_catalogue_js, name="error_catalogue_js"),
    path("sw.js", service_worker, name="service_worker"),
    path("manifest.json", manifest, name="manifest"),
    path("offline/", offline, name="offline"),
    path("billing/contact/", billing_contact, name="billing_contact"),
    path("webhooks/resend/", resend_webhook, name="resend_webhook"),
    path("webhooks/mailjet/", mailjet_webhook, name="mailjet_webhook"),
    path("admin/", admin.site.urls),
    path("", include("accounts.urls")),
    path("api/", include("api.urls")),
    path("settings/api/", include("api.urls_admin")),
    path("", include("platform_admin.urls")),
    path("", include("core.urls")),
    path("finance/", include("finance.urls")),
    path("fiscalisation/", include("fiscalisation.urls")),
    path("admissions/", include("admissions.urls")),
    path("lms/", include("lms.urls")),
    path("office/", include("operations.urls")),
    path("library/", include("library.urls")),
    path("health/", include("health.urls")),
    path("counselling/", include("counselling.urls")),
    path("student-affairs/", include("student_affairs.urls")),
    path("sports/", include("sports.urls")),
    path("boarding/", include("boarding.urls")),
    path("transport/", include("transport.urls")),
    path("catering/", include("catering.urls")),
    path("careers/", include("careers.urls")),
    path("examinations/", include("examinations.urls")),
    path("research/", include("research.urls")),
    path("labs/", include("labs.urls")),
    path("ict/", include("ict_helpdesk.urls")),
    path("ai/", include("ai.urls")),
    path("", include("academics.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
