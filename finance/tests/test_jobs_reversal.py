from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from academics.models import Programme, Student
from core.models import AuditEntry, Notification, OutboundMessage
from finance import services
from finance.models import (
    Instalment,
    InstalmentPlan,
    Invoice,
    JournalEntry,
    JournalLine,
    LatePenalty,
    LatePenaltyRule,
)

User = get_user_model()


class FinanceJobTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            "job-admin", password="pw123456", role="admin",
            is_staff=True, is_superuser=True)
        cls.programme = Programme.objects.create(
            name="Diploma in IT", code="DIT", level="diploma")
        cls.student_user = User.objects.create_user(
            "job-student", password="pw123456", role="student",
            email="student@example.com")
        cls.student = Student.objects.create(
            user=cls.student_user, programme=cls.programme,
            student_no="J9001", guardian_phone="+263771000000")
        cls.plan = InstalmentPlan.objects.create(
            student=cls.student, name="Term plan",
            total_amount=Decimal("400.00"))
        Instalment.objects.create(
            plan=cls.plan, due_date=timezone.localdate() - timedelta(days=20),
            amount=Decimal("400.00"))
        cls.rule = LatePenaltyRule.objects.create(
            name="Late fee", kind=LatePenaltyRule.Kind.FIXED,
            value=Decimal("25.00"), grace_days=5)

    def run_job(self, *args):
        out = StringIO()
        call_command("run_finance_jobs", *args, stdout=out)
        return out.getvalue()

    def test_job_applies_penalties_and_queues_reminders(self):
        output = self.run_job()
        self.assertIn("1 penalties", output)
        self.assertEqual(LatePenalty.objects.count(), 1)
        self.assertTrue(Invoice.objects.filter(
            description__startswith="Late penalty").exists())
        self.assertEqual(Notification.objects.filter(
            user=self.student_user, category="finance").count(), 1)
        messages = {m.channel: m for m in OutboundMessage.objects.all()}
        self.assertEqual(len(messages), 2)
        self.assertEqual(set(messages), {"email", "sms"})
        # Email is delivered through the configured provider. SMS is an honest
        # stub in this deployment, so it is recorded as failed rather than
        # pretending a guardian received a message.
        self.assertEqual(messages["email"].status, OutboundMessage.Status.SENT)
        self.assertEqual(messages["sms"].status, OutboundMessage.Status.FAILED)

    def test_second_run_on_the_same_day_does_not_duplicate(self):
        self.run_job()
        self.run_job()
        self.assertEqual(LatePenalty.objects.count(), 1)
        self.assertEqual(Notification.objects.filter(
            user=self.student_user, category="finance").count(), 1)
        self.assertEqual(OutboundMessage.objects.count(), 2)

    def test_job_can_skip_parts(self):
        output = self.run_job("--no-penalties", "--no-reminders")
        self.assertIn("0 penalties", output)
        self.assertEqual(LatePenalty.objects.count(), 0)
        self.assertEqual(Notification.objects.count(), 0)
        self.assertEqual(OutboundMessage.objects.count(), 0)

    def test_job_accepts_an_as_of_date(self):
        output = self.run_job("--as-of", "2020-01-01")
        self.assertIn("0 penalties", output)
        self.assertEqual(LatePenalty.objects.count(), 0)


class JournalReversalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            "rev-admin", password="pw123456", role="admin",
            is_staff=True, is_superuser=True)
        cls.bursar = User.objects.create_user(
            "rev-bursar", password="pw123456", role="bursar")
        cls.lecturer = User.objects.create_user(
            "rev-lecturer", password="pw123456", role="lecturer")
        cls.programme = Programme.objects.create(
            name="Diploma in IT", code="DIT", level="diploma")
        cls.student_user = User.objects.create_user(
            "rev-student", password="pw123456", role="student")
        cls.student = Student.objects.create(
            user=cls.student_user, programme=cls.programme,
            student_no="R9001")

    def make_invoice(self, amount="100.00"):
        return Invoice.objects.create(
            student=self.student, description="Tuition",
            amount=Decimal(amount), due_date=timezone.localdate())

    def test_reversal_posts_the_mirror_entry(self):
        invoice = self.make_invoice()
        entry = JournalEntry.objects.get(source_type="Invoice",
                                         source_id=str(invoice.pk))
        reversal = services.reverse_journal_entry(
            entry, "Posted to the wrong student", actor=self.admin)
        self.assertEqual(reversal.source_type, "Reversal")
        self.assertEqual(reversal.source_id, str(entry.pk))
        self.assertEqual(reversal.total_debit, entry.total_debit)
        self.assertEqual(reversal.total_credit, entry.total_credit)
        net = sum((line.debit - line.credit for line in JournalLine.objects.all()),
                  Decimal("0"))
        self.assertEqual(net, Decimal("0.00"))
        self.assertTrue(AuditEntry.objects.filter(
            action="finance.journal.reversed").exists())

    def test_a_reversal_cannot_be_repeated(self):
        invoice = self.make_invoice()
        entry = JournalEntry.objects.get(source_type="Invoice",
                                         source_id=str(invoice.pk))
        services.reverse_journal_entry(entry, "Correction", actor=self.admin)
        with self.assertRaises(ValidationError):
            services.reverse_journal_entry(entry, "Again", actor=self.admin)

    def test_a_reason_is_required(self):
        invoice = self.make_invoice()
        entry = JournalEntry.objects.get(source_type="Invoice",
                                         source_id=str(invoice.pk))
        with self.assertRaises(ValidationError):
            services.reverse_journal_entry(entry, "  ", actor=self.admin)

    def test_bursar_can_reverse_from_the_journal_page(self):
        invoice = self.make_invoice()
        entry = JournalEntry.objects.get(source_type="Invoice",
                                         source_id=str(invoice.pk))
        self.client.force_login(self.bursar)
        response = self.client.post(
            reverse("journal_reverse", args=[entry.pk]),
            {"reason": "Duplicate posting"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(JournalEntry.objects.filter(
            source_type="Reversal", source_id=str(entry.pk)).exists())

    def test_lecturer_cannot_reverse(self):
        invoice = self.make_invoice()
        entry = JournalEntry.objects.get(source_type="Invoice",
                                         source_id=str(invoice.pk))
        self.client.force_login(self.lecturer)
        response = self.client.post(
            reverse("journal_reverse", args=[entry.pk]), {"reason": "No"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(JournalEntry.objects.filter(
            source_type="Reversal", source_id=str(entry.pk)).exists())
