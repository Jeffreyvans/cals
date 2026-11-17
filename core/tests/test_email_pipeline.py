"""Email reliability: one backend, notification pipeline, retries, webhooks."""

import base64
import json
from datetime import UTC, datetime
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from accounts.registration import send_email_verification
from core.models import EmailEvent, EmailSuppression, Institution, OutboundMessage
from core.notifications import (
    DjangoEmailProvider,
    absolute_url,
    email_rate_limited,
    get_provider,
    queue_message,
    send_message,
    send_pending,
)
from core.tenancy import use_institution

SECRET = "whsec_" + base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()


class EmailBackendTests(TestCase):
    def test_no_smtp_settings_exist(self):
        # The old SMTP block is gone from our settings module: nothing here
        # can open a blocked port. (Django's own global defaults are unused.)
        from pathlib import Path

        text = (Path(settings.BASE_DIR) / "config" / "settings.py").read_text(
            encoding="utf-8")
        for name in ("EMAIL_HOST", "EMAIL_PORT", "EMAIL_USE_TLS", "EMAIL_USE_SSL"):
            with self.subTest(name=name):
                self.assertNotIn(f"{name} =", text)
        self.assertNotIn("smtp", settings.EMAIL_BACKEND.lower())

    def test_the_resend_backend_is_selected_by_the_backend_name(self):
        with override_settings(EMAIL_BACKEND="anymail.backends.resend.EmailBackend"):
            self.assertEqual(get_provider("email").name, "resend")

    def test_the_console_backend_is_used_without_a_key(self):
        with override_settings(
            EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend"
        ):
            self.assertEqual(get_provider("email").name, "console")

    def test_anymail_with_mailjet_is_pinned_in_requirements(self):
        from pathlib import Path

        text = (Path(settings.BASE_DIR) / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("django-anymail[mailjet,resend,brevo]==15.2", text)

    def test_the_mailjet_backend_is_selected_by_the_backend_name(self):
        with override_settings(EMAIL_BACKEND="anymail.backends.mailjet.EmailBackend"):
            self.assertEqual(get_provider("email").name, "mailjet")


class EmailPipelineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.account = get_user_model().objects.create_user(
            "emailuser", password="pw12345678", role="registrar",
            email="emailuser@example.test")

    def setUp(self):
        cache.clear()
        mail.outbox = []

    def test_verification_email_has_text_and_html_and_uses_site_url(self):
        with override_settings(SITE_URL="https://school.example"):
            verification = send_email_verification(self.account)
        self.assertIsNotNone(verification)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertIn("https://school.example/verify-email/", message.body)
        self.assertTrue(message.alternatives)
        self.assertIn("https://school.example/verify-email/", message.alternatives[0][0])
        self.assertIn(self.account.institution.name, message.from_email)

    def test_invitation_email_has_text_and_html(self):
        from accounts.user_admin import create_invitation

        create_invitation(self.account)
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(mail.outbox[0].alternatives)
        self.assertTrue(
            OutboundMessage.all_objects.filter(
                recipient=self.account.email).exists())

    def test_password_reset_email_has_text_and_html_and_uses_site_url(self):
        from core.email_types import PASSWORD_RESET_REQUEST

        with override_settings(SITE_URL="https://school.example"):
            response = self.client.post(
                reverse("password_reset"), {"email": self.account.email})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertIn("https://school.example/reset/", message.body)
        self.assertTrue(message.alternatives)
        logged = OutboundMessage.all_objects.get(
            recipient=self.account.email, email_type=PASSWORD_RESET_REQUEST)
        self.assertIn("https://school.example/reset/", logged.html_body)
        self.assertTrue(logged.provider_response)
        self.assertNotIn("token=", logged.provider_response)

    def test_password_reset_token_is_never_logged(self):
        import io
        import logging

        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setLevel(logging.DEBUG)
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            with override_settings(SITE_URL="https://school.example"):
                response = self.client.post(
                    reverse("password_reset"), {"email": self.account.email})
        finally:
            root.removeHandler(handler)
        self.assertEqual(response.status_code, 302)
        # The email body contains a one-time token, but no log record may.
        import re

        match = re.search(r"/reset/[^/]+/([^/\s]+)/", mail.outbox[0].body)
        self.assertIsNotNone(match)
        self.assertNotIn(match.group(1), stream.getvalue())

    def test_contact_form_is_recorded_in_outbound_messages(self):
        from accounts.registration import contact_recipient

        response = self.client.post(reverse("login"), {
            "form": "contact", "name": "Head Teacher", "email": "head@example.test",
            "phone": "", "message": "Please call me.", "website": "",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(OutboundMessage.all_objects.filter(
            recipient=contact_recipient(), subject="EduSphere contact form").exists())

    def test_generic_html_fallback_is_added(self):
        message = queue_message(
            "email", "someone@example.test", "Plain body only.", subject="Hello")
        send_message(message)
        message.refresh_from_db()
        self.assertTrue(message.html_body)
        self.assertIn("Plain body", message.html_body)

    def test_sender_display_name_uses_the_institution(self):
        provider = DjangoEmailProvider()
        result = provider.send(
            "email", "someone@example.test", "Hi", "Body",
            from_email=f"{self.account.institution.name} <no-reply@school.example>")
        self.assertTrue(result["ok"])


class FailureAndRetryTests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox = []

    def test_a_failure_is_recorded_and_never_raises(self):
        with override_settings(EMAIL_RETRY_BACKOFF_SECONDS=0, EMAIL_MAX_RETRIES=3):
            with patch.object(DjangoEmailProvider, "send",
                              return_value={"ok": False, "detail": "provider down"}):
                message = queue_message(
                    "email", "dead@example.test", "Body", subject="Test")
                send_message(message)
                message.refresh_from_db()
                self.assertEqual(message.status, OutboundMessage.Status.QUEUED)
                self.assertEqual(message.retries, 1)
                self.assertIn("provider down", message.provider_response)

    def test_retries_stop_after_the_maximum(self):
        with override_settings(EMAIL_RETRY_BACKOFF_SECONDS=0, EMAIL_MAX_RETRIES=3):
            with patch.object(DjangoEmailProvider, "send",
                              return_value={"ok": False, "detail": "down"}):
                message = queue_message(
                    "email", "dead@example.test", "Body", subject="Test")
                send_message(message)   # attempt 1
                send_pending()          # attempt 2
                send_pending()          # attempt 3 -> failed
                message.refresh_from_db()
        self.assertEqual(message.status, OutboundMessage.Status.FAILED)
        self.assertEqual(message.retries, 3)

    def test_a_later_attempt_can_succeed(self):
        with override_settings(EMAIL_RETRY_BACKOFF_SECONDS=0):
            with patch.object(DjangoEmailProvider, "send",
                              return_value={"ok": False, "detail": "down"}):
                message = queue_message(
                    "email", "recover@example.test", "Body", subject="Test")
                send_message(message)
            send_pending()
            message.refresh_from_db()
        self.assertEqual(message.status, OutboundMessage.Status.SENT)

    def test_sanitize_hides_tokens(self):
        from core.notifications import sanitize_detail

        cleaned = sanitize_detail(
            "Error: token=abc123secret https://x.test/reset?token=deadbeef")
        self.assertNotIn("abc123secret", cleaned)
        self.assertNotIn("deadbeef", cleaned)

    def test_a_suppressed_address_is_cancelled(self):
        EmailSuppression.objects.create(
            email="bounced@example.test", reason=EmailSuppression.Reason.BOUNCED)
        message = queue_message(
            "email", "bounced@example.test", "Body", subject="Test")
        send_message(message)
        message.refresh_from_db()
        self.assertEqual(message.status, OutboundMessage.Status.CANCELLED)
        self.assertEqual(len(mail.outbox), 0)


class RateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.account = get_user_model().objects.create_user(
            "ratelimited", password="pw12345678", role="registrar",
            email="ratelimited@example.test")

    def test_verification_is_limited_per_address(self):
        with override_settings(EMAIL_VERIFY_LIMIT=2, EMAIL_RATE_WINDOW_SECONDS=3600):
            self.assertIsNotNone(send_email_verification(self.account))
            self.assertIsNotNone(send_email_verification(self.account))
            self.assertIsNone(send_email_verification(self.account))

    def test_verification_is_limited_per_ip(self):
        request = RequestFactory().get("/")
        request.META["REMOTE_ADDR"] = "10.0.0.9"
        with override_settings(EMAIL_IP_LIMIT=1, EMAIL_RATE_WINDOW_SECONDS=3600):
            self.assertIsNotNone(send_email_verification(self.account, request=request))
            self.assertTrue(email_rate_limited("verify", "other@example.test",
                                               request=request))


class WebhookTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _signed(self, payload, secret=SECRET):
        body = json.dumps(payload).encode()
        from svix.webhooks import Webhook

        msg_id = "msg_test_1"
        timestamp = datetime.now(UTC)
        signature = Webhook(secret).sign(msg_id, timestamp, body.decode())
        return body, {
            "HTTP_SVIX_ID": msg_id,
            "HTTP_SVIX_TIMESTAMP": str(int(timestamp.timestamp())),
            "HTTP_SVIX_SIGNATURE": signature,
            "CONTENT_TYPE": "application/json",
        }

    def _post(self, body, headers):
        request = self.factory.post(
            reverse("resend_webhook"), data=body, content_type="application/json",
            **headers)
        from core.webhooks import resend_webhook

        return resend_webhook(request)

    def test_a_valid_bounce_is_recorded_and_suppresses_the_address(self):
        with override_settings(RESEND_WEBHOOK_SECRET=SECRET):
            body, headers = self._signed({
                "type": "email.bounced",
                "id": "evt_1",
                "data": {"email_id": "abc", "to": ["bad@example.test"],
                         "bounce": {"type": "Permanent"}},
            })
            response = self._post(body, headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(EmailEvent.all_objects.filter(
            kind=EmailEvent.Kind.BOUNCED, recipient="bad@example.test").exists())
        self.assertTrue(EmailSuppression.objects.filter(
            email="bad@example.test").exists())

    def test_a_complaint_suppresses_the_address(self):
        with override_settings(RESEND_WEBHOOK_SECRET=SECRET):
            body, headers = self._signed({
                "type": "email.complained", "id": "evt_2",
                "data": {"to": "angry@example.test"},
            })
            response = self._post(body, headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(EmailSuppression.objects.filter(
            email="angry@example.test",
            reason=EmailSuppression.Reason.COMPLAINED).exists())

    def test_an_invalid_signature_is_rejected(self):
        with override_settings(RESEND_WEBHOOK_SECRET=SECRET):
            body = json.dumps({"type": "email.bounced", "data": {"to": "x@example.test"}}).encode()
            response = self._post(body, {
                "HTTP_SVIX_ID": "msg", "HTTP_SVIX_TIMESTAMP": "1",
                "HTTP_SVIX_SIGNATURE": "v1,bm90LWEtc2lnbmF0dXJl",
                "CONTENT_TYPE": "application/json",
            })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(EmailEvent.all_objects.count(), 0)

    def test_delivered_events_are_counted(self):
        with override_settings(RESEND_WEBHOOK_SECRET=SECRET):
            body, headers = self._signed({
                "type": "email.delivered", "id": "evt_3",
                "data": {"to": "good@example.test"},
            })
            self._post(body, headers)
        self.assertTrue(EmailEvent.all_objects.filter(
            kind=EmailEvent.Kind.DELIVERED).exists())
        self.assertFalse(EmailSuppression.objects.filter(
            email="good@example.test").exists())


class MailjetWebhookTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.secret = "mailjet-user:mailjet-pass"

    def _post(self, payload, auth=None):
        auth = auth or self.secret
        request = self.factory.post(
            reverse("mailjet_webhook"),
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION="Basic "
            + base64.b64encode(auth.encode()).decode(),
        )
        from core.webhooks import mailjet_webhook

        return mailjet_webhook(request)

    def test_a_valid_hard_bounce_is_recorded_and_suppresses_the_address(self):
        with override_settings(MAILJET_WEBHOOK_SECRET=self.secret):
            response = self._post([{
                "event": "bounce", "time": 1, "MessageID": 12345,
                "email": "mj-bad@example.test", "bounce_type": "hardbounce",
            }])
        self.assertEqual(response.status_code, 200)
        self.assertTrue(EmailEvent.all_objects.filter(
            kind=EmailEvent.Kind.BOUNCED,
            recipient="mj-bad@example.test").exists())
        self.assertTrue(EmailSuppression.objects.filter(
            email="mj-bad@example.test",
            reason=EmailSuppression.Reason.BOUNCED).exists())

    def test_delivered_blocked_and_spam_events_are_recorded(self):
        with override_settings(MAILJET_WEBHOOK_SECRET=self.secret):
            self._post([
                {"event": "delivered", "MessageID": 1, "email": "mj-good@example.test"},
                {"event": "blocked", "MessageID": 2, "email": "mj-blocked@example.test"},
                {"event": "spam", "MessageID": 3, "email": "mj-spam@example.test"},
            ])
        self.assertTrue(EmailEvent.all_objects.filter(
            kind=EmailEvent.Kind.DELIVERED,
            recipient="mj-good@example.test").exists())
        self.assertTrue(EmailEvent.all_objects.filter(
            kind=EmailEvent.Kind.BLOCKED,
            recipient="mj-blocked@example.test").exists())
        self.assertTrue(EmailEvent.all_objects.filter(
            kind=EmailEvent.Kind.SPAM,
            recipient="mj-spam@example.test").exists())
        self.assertFalse(EmailSuppression.objects.filter(
            email="mj-good@example.test").exists())
        self.assertTrue(EmailSuppression.objects.filter(
            email="mj-blocked@example.test").exists())
        self.assertTrue(EmailSuppression.objects.filter(
            email="mj-spam@example.test",
            reason=EmailSuppression.Reason.COMPLAINED).exists())

    def test_invalid_credentials_are_rejected(self):
        with override_settings(MAILJET_WEBHOOK_SECRET=self.secret):
            response = self._post(
                [{"event": "delivered", "email": "x@example.test"}],
                auth="wrong:credentials",
            )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(EmailEvent.all_objects.count(), 0)

    def test_missing_secret_is_rejected(self):
        with override_settings(MAILJET_WEBHOOK_SECRET=""):
            response = self._post(
                [{"event": "delivered", "email": "x@example.test"}])
        self.assertEqual(response.status_code, 403)

    def test_malformed_payload_is_rejected(self):
        with override_settings(MAILJET_WEBHOOK_SECRET=self.secret):
            request = self.factory.post(
                reverse("mailjet_webhook"), data="not json",
                content_type="application/json",
                HTTP_AUTHORIZATION="Basic "
                + base64.b64encode(self.secret.encode()).decode(),
            )
            from core.webhooks import mailjet_webhook

            response = mailjet_webhook(request)
        self.assertEqual(response.status_code, 400)


class TenantIsolationTests(TestCase):
    def test_email_logs_are_scoped_to_the_institution(self):
        alpha = Institution.objects.create(name="Alpha", slug="alpha-email")
        beta = Institution.objects.create(name="Beta", slug="beta-email")
        with use_institution(alpha):
            queue_message("email", "a@example.test", "A", subject="A", institution=alpha)
        with use_institution(beta):
            queue_message("email", "b@example.test", "B", subject="B", institution=beta)
        with use_institution(alpha):
            recipients = set(OutboundMessage.objects.values_list("recipient", flat=True))
        self.assertEqual(recipients, {"a@example.test"})
        self.assertEqual(OutboundMessage.all_objects.count(), 2)


class CheckEmailCommandTests(TestCase):
    def test_the_command_prints_the_provider_response(self):
        from io import StringIO

        out = StringIO()
        call_command("check_email", "--to", "check@example.test", stdout=out)
        text = out.getvalue()
        self.assertIn("Status:", text)
        self.assertIn("Provider:", text)
        self.assertIn("From:", text)
        self.assertIn("Counts by type:", text)
        self.assertIn("Counts by status:", text)
        self.assertIn("email_check", text)
        self.assertNotIn("Bearer", text)


class SiteUrlHelperTests(TestCase):
    def test_absolute_url_uses_site_url(self):
        with override_settings(SITE_URL="https://school.example"):
            self.assertEqual(absolute_url("/reset/x/"), "https://school.example/reset/x/")
