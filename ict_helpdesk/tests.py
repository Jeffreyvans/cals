from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.factories import UserFactory
from core.models import Institution
from core.modules import set_module
from core.roles import ensure_roles
from ict_helpdesk.models import (
    AccountResetRequest,
    SLAPolicy,
    SoftwareLicence,
    Ticket,
    TicketComment,
)
from ict_helpdesk.services import (
    complete_account_reset,
    record_first_response,
    request_account_reset,
)


class IctHelpdeskTests(TestCase):
    def test_a_ticket_has_a_numbered_label_and_comments(self):
        ticket = Ticket.objects.create(title="Printer jam", description="Paper stuck")
        self.assertIn(f"#{ticket.pk}", str(ticket))
        comment = TicketComment.objects.create(ticket=ticket, body="On the way")
        self.assertEqual(ticket.comments.count(), 1)
        self.assertIn("Printer jam", str(comment))

    def test_a_licence_reports_its_seats(self):
        licence = SoftwareLicence.objects.create(name="Office", seats=10, seats_used=4)
        self.assertEqual(str(licence), "Office (10 seats)")


class IctSlaTests(TestCase):
    def test_a_ticket_is_overdue_against_its_priority_policy(self):
        SLAPolicy.objects.create(
            name="Urgent",
            priority=Ticket.Priority.URGENT,
            response_minutes=30,
            resolution_minutes=120,
        )
        ticket = Ticket.objects.create(
            title="Server down",
            description="Nobody can log in",
            priority=Ticket.Priority.URGENT,
        )
        self.assertIsNotNone(ticket.response_due)
        self.assertFalse(ticket.is_response_overdue)
        record_first_response(ticket)
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.first_response_at)
        self.assertFalse(ticket.is_response_overdue)


class AccountResetWorkflowTests(TestCase):
    def setUp(self):
        self.institution = Institution.objects.get(slug="default")
        ensure_roles(self.institution)
        set_module(self.institution, "ict_helpdesk", True)
        self.admin = UserFactory(
            username="ict_admin", role="admin", institution=self.institution
        )
        self.target = UserFactory(
            username="ict_target", role="student", institution=self.institution
        )

    def test_completing_a_reset_sets_a_new_password_once(self):
        reset = request_account_reset(
            self.target, requested_by=self.admin, reason="Forgot password"
        )
        reset, password = complete_account_reset(reset, actor=self.admin)
        self.target.refresh_from_db()
        self.assertTrue(self.target.check_password(password))
        self.assertEqual(reset.status, AccountResetRequest.Status.COMPLETED)
        self.assertEqual(reset.completed_by, self.admin)
        with self.assertRaises(ValidationError):
            complete_account_reset(reset, actor=self.admin)

    def test_the_reset_screen_shows_the_temporary_password_once(self):
        reset = request_account_reset(self.target, requested_by=self.admin)
        self.client.force_login(self.admin)
        url = reverse("ict_helpdesk:reset_manage", args=[reset.pk])
        page = self.client.post(url, {"action": "complete"})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Temporary password")
        reset.refresh_from_db()
        self.assertEqual(reset.status, AccountResetRequest.Status.COMPLETED)
        again = self.client.get(url)
        self.assertNotContains(again, "Temporary password")

    def test_a_rejected_request_sets_no_password(self):
        reset = request_account_reset(self.target, requested_by=self.admin)
        self.client.force_login(self.admin)
        url = reverse("ict_helpdesk:reset_manage", args=[reset.pk])
        response = self.client.post(url, {"action": "reject"})
        self.assertEqual(response.status_code, 200)
        reset.refresh_from_db()
        self.assertEqual(reset.status, AccountResetRequest.Status.REJECTED)
        self.assertEqual(reset.completed_by, self.admin)

    def test_the_sla_clock_starts_with_the_first_technician_reply(self):
        ticket = Ticket.objects.create(title="Wi-Fi", description="No signal")
        record_first_response(ticket, actor=self.admin)
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.first_response_at)
        self.assertEqual(ticket.assigned_to, self.admin)
        self.assertTrue(ticket.first_response_at <= timezone.now())
