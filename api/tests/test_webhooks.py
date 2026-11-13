"""Phase 8: webhooks - subscription, signing, delivery, retries and screens."""

import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from api.models import WebhookDelivery, WebhookEndpoint, sign_webhook
from api.webhooks import deliver, deliver_due, dispatch_event
from core.tenancy import use_institution

from .test_api import ApiTestCase, auth_header


def fake_response(status=200, body=b"ok"):
    response = MagicMock()
    response.status = status
    response.read.return_value = body
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


class DispatchTests(ApiTestCase):
    def setUp(self):
        self.endpoint = WebhookEndpoint.objects.create(
            institution=self.institution,
            name="Finance system",
            url="https://example.org/hooks/edusphere",
            secret=WebhookEndpoint.generate_secret(),
        )

    def test_creating_an_invoice_queues_a_delivery(self):
        from finance.models import Invoice

        with use_institution(self.institution):
            Invoice.objects.create(
                student=self.student, description="Lab fee", amount=10,
                due_date=timezone.localdate(), institution=self.institution,
            )
        delivery = WebhookDelivery.objects.filter(event="invoice.created").first()
        self.assertIsNotNone(delivery)
        self.assertEqual(delivery.status, WebhookDelivery.Status.PENDING)
        self.assertEqual(delivery.payload["student_no"], "S100")

    def test_unsubscribed_events_are_not_queued(self):
        self.endpoint.events = ["payment.received"]
        self.endpoint.save(update_fields=["events"])
        from finance.models import Invoice

        with use_institution(self.institution):
            Invoice.objects.create(
                student=self.student, description="Lab fee", amount=10,
                due_date=timezone.localdate(), institution=self.institution,
            )
        self.assertFalse(WebhookDelivery.objects.filter(event="invoice.created").exists())

    def test_a_paused_endpoint_receives_nothing(self):
        self.endpoint.is_active = False
        self.endpoint.save(update_fields=["is_active"])
        deliveries = dispatch_event("payment.received", {"id": 1}, self.institution)
        self.assertEqual(deliveries, [])

    def test_the_payload_is_signed_with_the_endpoint_secret(self):
        delivery = dispatch_event("payment.received", {"id": 7}, self.institution)[0]
        body = json.dumps(delivery.payload, separators=(",", ":"), sort_keys=True)
        timestamp = 1_800_000_000
        expected = hmac.new(
            self.endpoint.secret.encode(),
            f"{timestamp}.{body}".encode(),
            hashlib.sha256,
        ).hexdigest()
        header = sign_webhook(self.endpoint.secret, delivery.payload, timestamp)
        self.assertEqual(header, f"t={timestamp},v1={expected}")


class DeliveryTests(ApiTestCase):
    def setUp(self):
        self.endpoint = WebhookEndpoint.objects.create(
            institution=self.institution,
            name="Receiver",
            url="https://example.org/hooks/edusphere",
            secret="whsec_test",
        )
        self.delivery = WebhookDelivery.objects.create(
            endpoint=self.endpoint, event="payment.received", payload={"id": 1}
        )

    def test_a_successful_delivery_records_the_response(self):
        with patch("api.webhooks.urllib.request.urlopen", return_value=fake_response()) as send:
            deliver(self.delivery)
        self.delivery.refresh_from_db()
        self.assertEqual(self.delivery.status, WebhookDelivery.Status.DELIVERED)
        self.assertEqual(self.delivery.attempts, 1)
        self.assertEqual(self.delivery.response_status, 200)
        request = send.call_args.args[0]
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(headers["x-edusphere-event"], "payment.received")
        self.assertIn("v1=", headers["x-edusphere-signature"])
        self.assertEqual(request.method, "POST")

    def test_a_network_error_schedules_a_retry(self):
        with patch("api.webhooks.urllib.request.urlopen", side_effect=URLError("timeout")):
            deliver(self.delivery)
        self.delivery.refresh_from_db()
        self.assertEqual(self.delivery.status, WebhookDelivery.Status.FAILED)
        self.assertEqual(self.delivery.attempts, 1)
        self.assertGreater(self.delivery.next_attempt_at, timezone.now() - timedelta(seconds=1))
        self.assertIn("timeout", self.delivery.error)

    def test_an_http_error_records_the_status_code(self):
        error = HTTPError(self.endpoint.url, 500, "boom", {}, None)
        with patch("api.webhooks.urllib.request.urlopen", side_effect=error):
            deliver(self.delivery)
        self.delivery.refresh_from_db()
        self.assertEqual(self.delivery.status, WebhookDelivery.Status.FAILED)
        self.assertEqual(self.delivery.response_status, 500)

    def test_a_delivery_is_abandoned_after_the_maximum_attempts(self):
        self.delivery.attempts = 5
        with patch("api.webhooks.urllib.request.urlopen", side_effect=URLError("down")):
            deliver(self.delivery)
        self.delivery.refresh_from_db()
        self.assertEqual(self.delivery.attempts, 6)
        self.assertEqual(self.delivery.status, WebhookDelivery.Status.ABANDONED)

    def test_due_deliveries_are_sent_by_the_command(self):
        with patch("api.webhooks.urllib.request.urlopen", return_value=fake_response()):
            call_command("deliver_webhooks", verbosity=0)
        self.delivery.refresh_from_db()
        self.assertEqual(self.delivery.status, WebhookDelivery.Status.DELIVERED)

    def test_a_failed_delivery_is_not_retried_before_its_time(self):
        with patch("api.webhooks.urllib.request.urlopen", side_effect=URLError("down")):
            deliver(self.delivery)
        self.delivery.refresh_from_db()
        self.delivery.next_attempt_at = timezone.now() + timedelta(hours=1)
        self.delivery.save(update_fields=["next_attempt_at"])
        with patch("api.webhooks.urllib.request.urlopen", return_value=fake_response()) as send:
            sent = deliver_due()
        self.assertEqual(sent, 0)
        send.assert_not_called()

    def test_an_invalid_url_is_not_sent(self):
        self.endpoint.url = "ftp://example.org/hook"
        self.endpoint.save(update_fields=["url"])
        with patch("api.webhooks.urllib.request.urlopen") as send:
            deliver(self.delivery)
        send.assert_not_called()
        self.delivery.refresh_from_db()
        self.assertEqual(self.delivery.status, WebhookDelivery.Status.FAILED)


class WebhookScreenTests(ApiTestCase):
    def setUp(self):
        self.client.force_login(self.admin)
        self.endpoint = WebhookEndpoint.objects.create(
            institution=self.institution,
            name="Receiver",
            url="https://example.org/hooks/edusphere",
            secret="whsec_test",
        )

    def test_the_screens_need_the_settings_permission(self):
        self.client.force_login(self.student_user)
        response = self.client.get(reverse("api_keys"))
        self.assertEqual(response.status_code, 403)
        response = self.client.get(reverse("api_webhooks"))
        self.assertEqual(response.status_code, 403)

    def test_creating_a_key_shows_it_once(self):
        response = self.client.post(
            reverse("api_keys"),
            {"action": "create", "name": "Integration", "user": self.admin.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["raw_key"].startswith("esk_"))
        key = self.institution.api_keys.get(name="Integration")
        self.assertTrue(key.is_active)

    def test_revoking_a_key(self):
        self.client.post(
            reverse("api_keys"),
            {"action": "revoke", "key": self.admin_key.pk},
        )
        self.admin_key.refresh_from_db()
        self.assertFalse(self.admin_key.is_active)

    def test_creating_an_endpoint(self):
        response = self.client.post(
            reverse("api_webhooks"),
            {
                "name": "Ledger",
                "url": "https://example.org/ledger",
                "events": ["payment.received", "webhook.ping"],
            },
        )
        self.assertEqual(response.status_code, 302)
        endpoint = self.institution.webhook_endpoints.get(name="Ledger")
        self.assertEqual(endpoint.events, ["payment.received", "webhook.ping"])
        self.assertTrue(endpoint.secret.startswith("whsec_"))

    def test_a_webhook_must_be_http(self):
        self.client.post(
            reverse("api_webhooks"),
            {"name": "Bad", "url": "javascript:alert(1)"},
        )
        self.assertFalse(self.institution.webhook_endpoints.filter(name="Bad").exists())

    def test_the_detail_page_lists_deliveries(self):
        WebhookDelivery.objects.create(
            endpoint=self.endpoint, event="payment.received", payload={},
            status=WebhookDelivery.Status.DELIVERED,
        )
        response = self.client.get(reverse("api_webhook_detail", args=[self.endpoint.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "payment.received")

    def test_sending_a_test_event_uses_the_endpoint(self):
        with patch("api.webhooks.urllib.request.urlopen", return_value=fake_response()):
            response = self.client.post(
                reverse("api_webhook_detail", args=[self.endpoint.pk]),
                {"action": "test"},
            )
        self.assertEqual(response.status_code, 302)
        delivery = self.endpoint.deliveries.get(event="webhook.ping")
        self.assertEqual(delivery.status, WebhookDelivery.Status.DELIVERED)

    def test_rotating_the_secret(self):
        old = self.endpoint.secret
        self.client.post(
            reverse("api_webhook_detail", args=[self.endpoint.pk]),
            {"action": "rotate"},
        )
        self.endpoint.refresh_from_db()
        self.assertNotEqual(self.endpoint.secret, old)

    def test_retrying_a_failed_delivery(self):
        delivery = WebhookDelivery.objects.create(
            endpoint=self.endpoint, event="payment.received", payload={},
            status=WebhookDelivery.Status.FAILED, attempts=2,
        )
        with patch("api.webhooks.urllib.request.urlopen", return_value=fake_response()):
            self.client.post(
                reverse("api_webhook_detail", args=[self.endpoint.pk]),
                {"action": "retry", "delivery": delivery.pk},
            )
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, WebhookDelivery.Status.DELIVERED)

    def test_the_ping_api_endpoint(self):
        response = self.client.post(
            reverse("api_webhook_ping"), **auth_header(self.admin_raw)
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["queued"], 1)

    def test_the_ping_api_endpoint_needs_an_endpoint_and_permission(self):
        self.endpoint.delete()
        response = self.client.post(
            reverse("api_webhook_ping"), **auth_header(self.admin_raw)
        )
        self.assertEqual(response.status_code, 400)

    def test_deleting_an_endpoint(self):
        response = self.client.post(
            reverse("api_webhook_delete", args=[self.endpoint.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(WebhookEndpoint.objects.filter(pk=self.endpoint.pk).exists())
