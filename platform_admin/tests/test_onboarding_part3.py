"""Part 3 checks: failure handling and handover for institution onboarding.

A queued email can fail at the provider. The platform screens must say so
instead of reporting success, must not mark the welcome email as delivered,
and must still hand the administrator a one-time credential. These tests use
the same ``OutboundMessage.status != "sent"`` rule as the views.
"""

from __future__ import annotations

from unittest.mock import patch

from django.urls import reverse

from core.models import OutboundMessage
from core.roles import ensure_roles
from platform_admin.models import InstitutionOnboarding
from platform_admin.services import approve_application, create_application

from .base import PlatformTestBase


def fail_delivery(message):
    """Stand in for a provider that refused the message."""
    message.status = OutboundMessage.Status.FAILED
    message.save(update_fields=["status"])
    return message


class EmailFailureTests(PlatformTestBase):
    def test_a_failed_welcome_resend_reports_failure_and_is_not_counted(self):
        row = InstitutionOnboarding.objects.get_or_create(institution=self.inst)[0]
        self.login(self.main)
        url = reverse("platform_admin:institution_detail", args=[self.inst.pk])
        with patch("platform_admin.onboarding.send_message", side_effect=fail_delivery):
            response = self.client.post(url, {"action": "resend_welcome", "user": self.inst_admin.pk})
        self.assertEqual(response.status_code, 302)
        row.refresh_from_db()
        self.assertIsNone(row.welcome_email_sent_at)
        self.assertEqual(row.welcome_email_count, 0)
        page = self.client.get(url)
        self.assertContains(page, "We could not send the email")

    def test_a_failed_login_link_email_reports_failure(self):
        self.login(self.main)
        url = reverse("platform_admin:institution_detail", args=[self.inst.pk])
        with patch("platform_admin.onboarding.send_message", side_effect=fail_delivery):
            response = self.client.post(
                url, {"action": "email_login_link", "user": self.inst_admin.pk}
            )
        self.assertEqual(response.status_code, 302)
        page = self.client.get(url)
        self.assertContains(page, "We could not send the email")
        message = OutboundMessage.objects.filter(
            recipient=self.inst_admin.email, subject__icontains="Sign in"
        ).first()
        self.assertEqual(message.status, OutboundMessage.Status.FAILED)

    def test_a_failed_invitation_resend_still_shows_the_one_time_link(self):
        self.login(self.main)
        url = reverse("platform_admin:institution_admins", args=[self.inst.pk])
        with patch("accounts.user_admin.send_message", side_effect=fail_delivery):
            response = self.client.post(
                url, {"action": "resend_invitation", "user": self.inst_admin.pk}
            )
        self.assertEqual(response.status_code, 302)
        page = self.client.get(response["Location"])
        self.assertContains(page, "We could not send the email")
        self.assertContains(page, "Show a one-time link")
        self.assertContains(page, "onboarding-secret")

    def test_a_failed_password_reset_still_shows_the_temporary_password(self):
        self.login(self.main)
        url = reverse("platform_admin:institution_admins", args=[self.inst.pk])
        with patch("accounts.user_admin.send_message", side_effect=fail_delivery):
            response = self.client.post(
                url, {"action": "reset_password", "user": self.inst_admin.pk}
            )
        self.assertEqual(response.status_code, 302)
        page = self.client.get(response["Location"])
        self.assertContains(page, "We could not send the email")
        self.assertContains(page, "Show a one-time link")
        self.assertContains(page, "onboarding-secret")


class ApprovalEmailTests(PlatformTestBase):
    def setUp(self):
        super().setUp()
        ensure_roles(self.inst)

    def test_the_approval_follow_up_uses_the_support_contacts(self):
        application = create_application(self.application_payload(agreed_to_terms=True))
        with patch("platform_admin.services._application_email") as email:
            approve_application(application, self.main)
        call = next(
            entry for entry in email.call_args_list
            if "Welcome to EduSphere" in entry.args[1]
        )
        self.assertIn("+263774573765", call.args[3])
        self.assertIn("vanhuvaonejeffrey@gmail.com", call.args[3])
        self.assertEqual(call.args[4]["support_phone"], "+263774573765")
        self.assertEqual(call.args[4]["support_email"], "vanhuvaonejeffrey@gmail.com")

    def test_no_audit_row_from_an_approval_contains_a_one_time_link(self):
        from django.contrib.auth import get_user_model

        from platform_admin.models import PlatformAuditLog

        application = create_application(self.application_payload(agreed_to_terms=True))
        with patch("platform_admin.services._application_email"):
            institution = approve_application(application, self.main)
        admin = get_user_model().objects.get(institution=institution, role="admin")
        invitation = admin.invitations.first()
        payload = str(list(PlatformAuditLog.objects.filter(institution=institution).values()))
        self.assertNotIn(str(invitation.token), payload)
        self.assertNotIn("token=", payload)
