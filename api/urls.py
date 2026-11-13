"""Public REST API v1 and the interactive schema."""

from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("students", views.StudentViewSet, basename="student")
router.register("staff", views.StaffViewSet, basename="staff")
router.register("academic-years", views.AcademicYearViewSet, basename="academic-year")
router.register("terms", views.TermViewSet, basename="term")
router.register("programmes", views.ProgrammeViewSet, basename="programme")
router.register("courses", views.CourseViewSet, basename="course")
router.register("subjects", views.SubjectViewSet, basename="subject")
router.register("classes", views.SchoolClassViewSet, basename="class")
router.register("enrolments", views.EnrollmentViewSet, basename="enrolment")
router.register("attendance", views.AttendanceViewSet, basename="attendance")
router.register("invoices", views.InvoiceViewSet, basename="invoice")
router.register("payments", views.PaymentViewSet, basename="payment")
router.register("applications", views.ApplicationViewSet, basename="application")

urlpatterns = [
    path("schema/", SpectacularAPIView.as_view(), name="api_schema"),
    path(
        "docs/",
        SpectacularSwaggerView.as_view(url_name="api_schema"),
        name="api_docs",
    ),
    path("redoc/", SpectacularRedocView.as_view(url_name="api_schema"), name="api_redoc"),
    path("v1/whoami/", views.WhoAmIView.as_view(), name="api_whoami"),
    path(
        "v1/attendance/device/",
        views.AttendanceDeviceView.as_view(),
        name="api_attendance_device",
    ),
    path("v1/webhooks/ping/", views.WebhookPingView.as_view(), name="api_webhook_ping"),
    path(
        "v1/finance/journal/export/",
        views.JournalExportView.as_view(),
        name="api_journal_export",
    ),
    path("v1/", include(router.urls)),
]
