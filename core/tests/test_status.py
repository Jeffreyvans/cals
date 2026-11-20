"""Tests for the public system status page."""

from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from core.status import (
    DEGRADED,
    NOT_CONFIGURED,
    OPERATIONAL,
    OUTAGE,
    system_status_snapshot,
)


class SystemStatusTests(TestCase):
    def test_status_page_is_public_and_does_not_cache(self):
        response = self.client.get(reverse("system_status"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "System status")
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertContains(response, "Database")

    def test_a_database_outage_reports_an_outage(self):
        with patch("core.status._database", return_value=(OUTAGE, "down")):
            snapshot = system_status_snapshot()
        self.assertEqual(snapshot["overall"], OUTAGE)
        self.assertEqual(snapshot["overall_label"], "Outage")

    def test_development_backends_are_reported_as_not_configured(self):
        with override_settings(
            EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
            STORAGES={"default": {
                "BACKEND": "django.core.files.storage.FileSystemStorage",
            }},
            PAYNOW_INTEGRATION_ID="",
            PAYNOW_INTEGRATION_KEY="",
        ):
            snapshot = system_status_snapshot()
        by_name = {row["name"]: row["status"] for row in snapshot["components"]}
        self.assertEqual(by_name["Email delivery"], NOT_CONFIGURED)
        self.assertEqual(by_name["Object storage"], NOT_CONFIGURED)
        self.assertEqual(by_name["Online payments"], NOT_CONFIGURED)
        self.assertEqual(snapshot["overall"], DEGRADED)

    def test_a_fully_configured_live_platform_reports_operational(self):
        with override_settings(
            EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
            STORAGES={"default": {"BACKEND": "storages.backends.s3.S3Storage"}},
            PAYNOW_INTEGRATION_ID="123",
            PAYNOW_INTEGRATION_KEY="secret",
            PAYNOW_MODE="live",
        ), patch("core.status._email", return_value=(OPERATIONAL, "ok")):
            snapshot = system_status_snapshot()
        self.assertEqual(snapshot["overall"], OPERATIONAL)
