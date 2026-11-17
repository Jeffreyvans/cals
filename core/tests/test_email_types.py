"""Every email type is typed, logged and delivered through the central sender."""

from django.core import mail
from django.test import TestCase

from core.email_types import EMAIL_TYPE_CODES, EMAIL_TYPES
from core.models import OutboundMessage
from core.notifications import send_email

# Mirrors the audit table in DEPLOY.md. A new email type must be added here, in
# core/email_types.py and in DEPLOY.md so observability cannot drift.
DOCUMENTED_TYPES = {
    "general",
    "email_verification",
    "resend_verification",
    "password_reset_request",
    "password_reset_confirmation",
    "password_change_notice",
    "forced_password_change_notice",
    "staff_invitation",
    "student_invitation",
    "set_password_link",
    "signup_link",
    "registration_request_decision",
    "student_application_received",
    "institution_application_received",
    "institution_application_approved",
    "institution_application_more_info",
    "institution_application_rejected",
    "platform_access_request",
    "platform_access_decision",
    "billing_reminder",
    "suspension_warning",
    "fee_reminder",
    "fee_receipt",
    "results_notification",
    "absence_notification",
    "library_overdue",
    "contact_form",
    "demo_request",
    "announcement",
    "security_alert",
    "admin_error",
    "welcome",
    "account_created",
    "role_changed",
    "email_check",
    "other",
}


class EmailTypeRegistryTests(TestCase):
    def test_registry_matches_the_documented_audit_table(self):
        self.assertEqual(set(EMAIL_TYPE_CODES), DOCUMENTED_TYPES)
        self.assertEqual(len(EMAIL_TYPES), len(DOCUMENTED_TYPES))
        for code, label in EMAIL_TYPES:
            self.assertTrue(code)
            self.assertTrue(label)


class EveryEmailTypeTests(TestCase):
    def setUp(self):
        mail.outbox = []

    def test_every_type_sends_plain_text_and_html_through_one_pipeline(self):
        for index, email_type in enumerate(sorted(EMAIL_TYPE_CODES)):
            with self.subTest(email_type=email_type):
                recipient = f"type-{index}@example.test"
                message = send_email(
                    email_type, recipient, f"Subject {email_type}",
                    f"Plain body for {email_type}.",
                )
                self.assertEqual(message.status, OutboundMessage.Status.SENT)
                self.assertEqual(message.email_type, email_type)
                self.assertTrue(message.body)
                self.assertTrue(message.html_body)
                self.assertIn(message.provider, {"locmem", "console"})
                sent = mail.outbox[-1]
                self.assertEqual(sent.to, [recipient])
                self.assertIn("Plain body", sent.body)
                self.assertTrue(sent.alternatives)
                self.assertEqual(sent.alternatives[0][1], "text/html")

    def test_unknown_types_are_rejected_before_a_message_is_created(self):
        before = OutboundMessage.all_objects.count()
        with self.assertRaises(ValueError):
            send_email("not-a-real-type", "x@example.test", "S", "B")
        self.assertEqual(OutboundMessage.all_objects.count(), before)
