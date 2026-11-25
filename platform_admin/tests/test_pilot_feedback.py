"""Pilot feedback travels from a signed-in page to the platform Inbox."""

from __future__ import annotations

import re

from django.core.cache import cache
from django.urls import reverse

from core.models import Notification
from platform_admin.models import PlatformRequest

from .base import PlatformTestBase


class PilotFeedbackTests(PlatformTestBase):
    def setUp(self):
        super().setUp()
        cache.clear()

    def test_button_is_visible_on_signed_in_pages(self):
        self.login(self.inst_admin)
        response = self.client.get(reverse("help_center"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pilot feedback")
        self.assertContains(response, reverse("platform_admin:pilot_feedback"))

    def test_anonymous_visitors_must_sign_in(self):
        response = self.client.get(reverse("platform_admin:pilot_feedback"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_form_prefills_the_page_and_error_reference(self):
        self.login(self.inst_admin)
        response = self.client.get(
            reverse("platform_admin:pilot_feedback"),
            {"next": "/academics/students/", "error": "ERR-ABC123"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="page" value="/academics/students/"')
        self.assertContains(response, 'name="error_reference" value="ERR-ABC123"')

    def test_feedback_is_written_to_the_platform_inbox(self):
        self.login(self.inst_admin)
        response = self.client.post(
            reverse("platform_admin:pilot_feedback"),
            {
                "message": "The report card button did nothing on the first click.",
                "error_reference": "ERR-A1B2C3",
                "page": "/students/12/report/",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/students/12/report/")
        item = PlatformRequest.objects.get(kind=PlatformRequest.Kind.FEEDBACK)
        self.assertEqual(item.institution, self.inst)
        self.assertEqual(item.requested_by, self.inst_admin)
        self.assertEqual(item.status, PlatformRequest.Status.OPEN)
        self.assertEqual(item.reference, "ERR-A1B2C3")
        self.assertEqual(item.payload, {"page": "/students/12/report/"})
        self.assertIn("report card button", item.detail)
        self.assertTrue(
            Notification.objects.filter(user=self.main, category="feedback").exists()
        )

        self.login(self.main)
        inbox = self.client.get(reverse("platform_admin:inbox"))
        self.assertContains(inbox, "Pilot feedback")
        detail = self.client.get(
            reverse("platform_admin:platform_request_detail", args=[item.pk])
        )
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "The report card button did nothing")

    def test_invalid_reference_and_offsite_page_are_not_accepted(self):
        self.login(self.inst_admin)
        response = self.client.post(
            reverse("platform_admin:pilot_feedback"),
            {
                "message": "Something went wrong here.",
                "error_reference": "bad-ref-123",
                "page": "https://example.org/steal",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PlatformRequest.objects.exists())
        self.assertContains(response, "Enter the reference shown on the error page.")

    def test_offsite_next_is_ignored(self):
        self.login(self.inst_admin)
        response = self.client.get(
            reverse("platform_admin:pilot_feedback"), {"next": "//example.org/steal"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="page" value="//example.org')

    def test_too_many_reports_are_stopped(self):
        self.login(self.inst_admin)
        for index in range(5):
            self.client.post(
                reverse("platform_admin:pilot_feedback"),
                {"message": f"Report number {index} about this page.", "page": "/"},
            )
        self.assertEqual(
            PlatformRequest.objects.filter(kind=PlatformRequest.Kind.FEEDBACK).count(), 5
        )
        response = self.client.post(
            reverse("platform_admin:pilot_feedback"),
            {"message": "One report too many.", "page": "/"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You have sent several reports")
        self.assertEqual(
            PlatformRequest.objects.filter(kind=PlatformRequest.Kind.FEEDBACK).count(), 5
        )

    def test_error_page_reference_reaches_the_feedback_form(self):
        self.login(self.inst_admin)
        response = self.client.get("/this-page-does-not-exist/")
        self.assertEqual(response.status_code, 404)
        reference = re.search(r"ERR-[0-9A-F]{6}", response.content.decode()).group(0)
        self.assertIn("Pilot feedback", response.content.decode())
        form = self.client.get(
            reverse("platform_admin:pilot_feedback"), {"next": "/this-page-does-not-exist/", "error": reference}
        )
        self.assertContains(form, f'name="error_reference" value="{reference}"')
