"""Phase 1: the notification bell, the notifications page, and the audit trail."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.audit import log_action, log_sensitive_view
from core.models import AuditEntry, Notification, OutboundMessage, SensitiveViewLog
from core.notifications import (
    mark_all_read,
    mark_read,
    notify,
    queue_message,
    send_pending,
    unread_count,
)

User = get_user_model()


class NotificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("bell", password="pw123456", role="registrar")
        cls.other = User.objects.create_user("bell2", password="pw123456", role="registrar")

    def test_a_notification_is_created_for_the_user(self):
        notify(self.user, "Your request was approved", "Refund REF-1 was approved.")
        self.assertEqual(Notification.objects.filter(user=self.user).count(), 1)
        self.assertEqual(unread_count(self.user), 1)
        self.assertEqual(unread_count(self.other), 0)

    def test_the_bell_shows_the_unread_count(self):
        notify(self.user, "One")
        notify(self.user, "Two")
        self.client.force_login(self.user)
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Notifications")
        self.assertContains(resp, ">2<")

    def test_the_notifications_page_lists_only_your_own(self):
        notify(self.user, "Mine")
        notify(self.other, "Theirs")
        self.client.force_login(self.user)
        resp = self.client.get(reverse("notifications"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Mine")
        self.assertNotContains(resp, "Theirs")

    def test_a_notification_can_be_marked_read(self):
        note = notify(self.user, "Read me")
        self.assertTrue(mark_read(self.user, note.pk))
        self.assertIsNotNone(Notification.objects.get(pk=note.pk).read_at)
        self.assertEqual(unread_count(self.user), 0)

    def test_you_cannot_mark_somebody_elses_notification_read(self):
        note = notify(self.other, "Theirs")
        self.assertFalse(mark_read(self.user, note.pk))
        self.assertIsNone(Notification.objects.get(pk=note.pk).read_at)

    def test_mark_all_read_clears_the_bell(self):
        notify(self.user, "One")
        notify(self.user, "Two")
        mark_all_read(self.user)
        self.assertEqual(unread_count(self.user), 0)


class OutboundMessageTests(TestCase):
    def test_a_queued_message_is_sent_by_the_pending_worker(self):
        message = queue_message(
            "sms", "+263770000000", "Your receipt is ready.",
            subject="EduSphere", institution=self._institution())
        self.assertEqual(message.status, OutboundMessage.Status.QUEUED)
        sent = send_pending()
        self.assertEqual(sent, 1)
        message.refresh_from_db()
        self.assertEqual(message.status, OutboundMessage.Status.SENT)
        self.assertIsNotNone(message.sent_at)

    def _institution(self):
        from core.models import Institution

        return Institution.objects.filter(slug="default").first()


class AuditTrailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            "auditor", password="pw123456", role="admin", is_staff=True, is_superuser=True)
        cls.bursar = User.objects.create_user("bur", password="pw123456", role="bursar")

    def test_an_action_is_written_to_the_trail(self):
        log_action(self.admin, "finance.payment_recorded", after={"amount": 100})
        self.assertTrue(
            AuditEntry.all_objects.filter(action="finance.payment_recorded").exists()
        )

    def test_the_trail_page_shows_the_entry(self):
        log_action(self.admin, "finance.payment_recorded", after={"amount": 100})
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("audit_trail"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "finance.payment_recorded")

    def test_the_trail_can_be_filtered_by_action(self):
        log_action(self.admin, "one.action")
        log_action(self.admin, "two.action")
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("audit_trail"), {"action": "two.action"})
        content = resp.content.decode()
        self.assertIn("<code>two.action</code>", content)
        self.assertNotIn("<code>one.action</code>", content)

    def test_the_trail_can_be_exported_as_csv(self):
        log_action(self.admin, "one.action")
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("audit_trail_export"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/csv")
        self.assertIn("one.action", resp.content.decode())

    def test_a_bursar_can_read_the_trail_but_a_lecturer_cannot(self):
        self.client.force_login(self.bursar)
        self.assertEqual(self.client.get(reverse("audit_trail")).status_code, 200)
        lecturer = User.objects.create_user("lec", password="pw123456", role="lecturer")
        self.client.force_login(lecturer)
        self.assertEqual(self.client.get(reverse("audit_trail")).status_code, 403)

    def test_a_restricted_view_is_logged_separately(self):
        self.client.force_login(self.admin)
        log_sensitive_view(self.client.get(reverse("dashboard")).wsgi_request,
                           "StudentHealth", 42, "health record")
        self.assertTrue(
            SensitiveViewLog.all_objects.filter(record_type="StudentHealth").exists()
        )
        resp = self.client.get(reverse("sensitive_log"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "StudentHealth")

    def test_the_trail_of_one_institution_is_hidden_from_another(self):
        from core.models import Institution

        other = Institution.objects.create(name="Beta School", slug="beta", type="secondary")
        entry = AuditEntry.objects.create(
            institution=other, actor=self.admin, action="other.action")
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("audit_trail"))
        self.assertNotContains(resp, "other.action")
        self.assertNotEqual(entry.institution_id, self.admin.institution_id)
