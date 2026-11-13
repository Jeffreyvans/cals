from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from academics.factories import ProgrammeFactory, StudentFactory
from accounts.factories import UserFactory
from core.models import Institution, Notification, OutboundMessage, StaffProfile
from core.modules import set_module
from core.roles import ensure_roles
from finance.models import JournalEntry
from operations.models import (
    LeaveRequest,
    Newsletter,
    PayrollRule,
    PayrollRun,
    SalaryStructure,
)
from operations.registry import MODULES
from operations.services import (
    approve_payroll_run,
    decide_leave,
    generate_payslips,
    post_payroll_run,
    send_newsletter,
)


class OperationsServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.institution = Institution.objects.get(slug="default")
        ensure_roles(cls.institution)
        for code, info in MODULES.items():
            set_module(cls.institution, info.get("flag", code), True)
        cls.admin = UserFactory(
            username="ops_service_admin", role="admin", institution=cls.institution
        )
        cls.staff_user = UserFactory(
            username="ops_staff", role="lecturer", institution=cls.institution
        )
        cls.staff = StaffProfile.objects.create(
            institution=cls.institution,
            user=cls.staff_user,
            employee_no="EMP-001",
            job_title="Teacher",
        )

    def test_leave_decision_sets_the_decision_and_notifies_the_staff_member(self):
        leave = LeaveRequest.objects.create(
            institution=self.institution,
            staff=self.staff,
            kind=LeaveRequest.Kind.SICK,
            start_date=date(2026, 2, 2),
            end_date=date(2026, 2, 4),
            reason="Flu",
        )
        decide_leave(leave, self.admin, True, "Get well soon")
        leave.refresh_from_db()
        self.assertEqual(leave.status, LeaveRequest.Status.APPROVED)
        self.assertEqual(leave.decided_by, self.admin)
        self.assertEqual(leave.decided_at is not None, True)
        self.assertEqual(leave.decision_note, "Get well soon")
        notification = Notification.objects.get(user=self.staff_user)
        self.assertIn("approved", notification.title.lower())
        self.assertIn("2026-02-02", notification.body)

    def test_a_leave_decision_cannot_be_changed(self):
        leave = LeaveRequest.objects.create(
            institution=self.institution,
            staff=self.staff,
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 1),
        )
        decide_leave(leave, self.admin, False)
        with self.assertRaises(ValidationError):
            decide_leave(leave, self.admin, True)

    def test_generate_payslips_uses_the_active_tax_and_pension_rules(self):
        PayrollRule.objects.create(
            institution=self.institution, kind=PayrollRule.Kind.TAX, name="PAYE", rate=Decimal("20")
        )
        PayrollRule.objects.create(
            institution=self.institution,
            kind=PayrollRule.Kind.PENSION,
            name="Pension",
            rate=Decimal("5"),
        )
        SalaryStructure.objects.create(
            institution=self.institution,
            staff=self.staff,
            basic=Decimal("1000"),
            housing_allowance=Decimal("100"),
            transport_allowance=Decimal("100"),
        )
        run = PayrollRun.objects.create(
            institution=self.institution,
            name="January 2026",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            pay_date=date(2026, 1, 25),
            created_by=self.admin,
        )
        self.assertEqual(generate_payslips(run), 1)
        slip = run.payslips.get()
        self.assertEqual(slip.basic, Decimal("1000.00"))
        self.assertEqual(slip.allowances, Decimal("200.00"))
        self.assertEqual(slip.gross, Decimal("1200.00"))
        self.assertEqual(slip.tax, Decimal("240.00"))
        self.assertEqual(slip.pension, Decimal("50.00"))
        self.assertEqual(slip.net, Decimal("910.00"))
        self.assertEqual(generate_payslips(run), 0)

    def test_payroll_is_approved_then_posted_to_the_ledger_once(self):
        SalaryStructure.objects.create(
            institution=self.institution, staff=self.staff, basic=Decimal("500")
        )
        run = PayrollRun.objects.create(
            institution=self.institution,
            name="February 2026",
            start_date=date(2026, 2, 1),
            end_date=date(2026, 2, 28),
            pay_date=date(2026, 2, 25),
            created_by=self.admin,
        )
        with self.assertRaises(ValidationError):
            approve_payroll_run(run, self.admin)
        generate_payslips(run)
        approve_payroll_run(run, self.admin)
        run.refresh_from_db()
        self.assertEqual(run.status, PayrollRun.Status.APPROVED)
        entry = post_payroll_run(run, self.admin)
        run.refresh_from_db()
        self.assertEqual(run.status, PayrollRun.Status.PAID)
        self.assertEqual(run.posted_journal, entry)
        self.assertEqual(entry.source_type, "Payroll")
        self.assertEqual(
            JournalEntry.all_objects.filter(
                institution=self.institution, source_type="Payroll"
            ).count(),
            1,
        )
        with self.assertRaises(ValidationError):
            post_payroll_run(run, self.admin)

    def test_a_newsletter_is_queued_for_students_and_staff_once(self):
        StudentFactory(
            user__email="learner@example.com", status="active", programme=ProgrammeFactory()
        )
        newsletter = Newsletter.objects.create(
            institution=self.institution,
            title="Open day",
            subject="Come and see us",
            body="Our open day is on Friday.",
            created_by=self.admin,
        )
        self.assertEqual(send_newsletter(newsletter, self.admin), 2)
        newsletter.refresh_from_db()
        self.assertEqual(newsletter.status, Newsletter.Status.SENT)
        self.assertTrue(newsletter.sent_at)
        recipients = set(OutboundMessage.objects.values_list("recipient", flat=True))
        self.assertIn("learner@example.com", recipients)
        self.assertIn("ops_staff@example.com", recipients)
        with self.assertRaises(ValidationError):
            send_newsletter(newsletter, self.admin)

