from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from academics.models import Programme, Student
from finance.models import Invoice, Payment, Waiver

User = get_user_model()
TODAY = date.today()


class BaseWaivers(TestCase):
    def setUp(self):
        self.bursar = User.objects.create_user("bursar", password="pw123456",
                                               role="bursar")
        self.registrar = User.objects.create_user("registrar", password="pw123456",
                                                  role="registrar")
        self.lecturer = User.objects.create_user("lecturer", password="pw123456",
                                                 role="lecturer")
        self.student_user = User.objects.create_user("alice", password="pw123456",
                                                     role="student")
        programme = Programme.objects.create(name="Diploma in IT", code="DIT",
                                             level="diploma")
        self.student = Student.objects.create(user=self.student_user,
                                              programme=programme, student_no="S0501")
        self.invoice = Invoice.objects.create(student=self.student,
                                              description="Tuition",
                                              amount=Decimal("500.00"),
                                              due_date=TODAY - timedelta(days=10))
        Payment.objects.create(invoice=self.invoice, amount=Decimal("100.00"))

    def waive_url(self):
        return reverse("waive", args=[self.invoice.pk])


class WaiverIssueTests(BaseWaivers):
    def test_bursar_issues_a_credit_note(self):
        self.client.force_login(self.bursar)
        resp = self.client.post(self.waive_url(),
                                {"amount": "150.00", "reason": "Hardship bursary"},
                                follow=True)
        self.assertEqual(resp.status_code, 200)
        w = Waiver.objects.get()
        self.assertTrue(w.note_no.startswith("CN"))
        self.assertEqual(w.recorded_by, self.bursar)
        self.assertEqual(w.amount, Decimal("150.00"))
        self.assertEqual(self.invoice.balance, Decimal("250.00"))
        self.assertEqual(self.student.balance, Decimal("250.00"))
        self.assertContains(resp, w.note_no)

    def test_zero_and_negative_amounts_are_rejected(self):
        self.client.force_login(self.bursar)
        for value in ("0", "-25"):
            resp = self.client.post(self.waive_url(),
                                    {"amount": value, "reason": "test"})
            self.assertFormError(resp.context["form"], "amount",
                                 "Amount must be above zero.")
        self.assertEqual(Waiver.objects.count(), 0)

    def test_waiving_more_than_is_owed_is_rejected(self):
        self.client.force_login(self.bursar)
        resp = self.client.post(self.waive_url(),
                                {"amount": "400.01", "reason": "too much"})
        self.assertFormError(resp.context["form"], "amount",
                             "Amount exceeds what is still owed on this invoice "
                             "(400.00).")
        self.assertEqual(Waiver.objects.count(), 0)

    def test_partials_can_be_waived_until_exhausted(self):
        self.client.force_login(self.bursar)
        for amount in ("300.00", "100.00"):
            resp = self.client.post(self.waive_url(),
                                    {"amount": amount, "reason": "partial"},
                                    follow=True)
            self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.invoice.balance, Decimal("0"))
        self.assertEqual(self.student.balance, Decimal("0"))
        resp = self.client.post(self.waive_url(),
                                {"amount": "0.01", "reason": "anything"})
        self.assertFormError(resp.context["form"], "amount",
                             "Amount exceeds what is still owed on this invoice "
                             "(0.00).")
        self.assertEqual(Waiver.objects.count(), 2)

    def test_only_the_bursar_may_issue(self):
        for u in (self.registrar, self.lecturer, self.student_user):
            self.client.force_login(u)
            resp = self.client.post(self.waive_url(),
                                    {"amount": "50", "reason": "nope"})
            self.assertEqual(resp.status_code, 403, u.username)
        self.assertEqual(Waiver.objects.count(), 0)

    def test_registrar_may_read_a_credit_note(self):
        w = Waiver.objects.create(invoice=self.invoice, amount=Decimal("10.00"),
                                  reason="Test", recorded_by=self.bursar)
        self.client.force_login(self.registrar)
        resp = self.client.get(reverse("credit_note", args=[w.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, w.note_no)

    def test_students_cannot_read_a_credit_note(self):
        w = Waiver.objects.create(invoice=self.invoice, amount=Decimal("10.00"),
                                  reason="Test", recorded_by=self.bursar)
        self.client.force_login(self.student_user)
        self.assertEqual(self.client.get(reverse("credit_note", args=[w.pk])).status_code,
                         403)

    def test_waiver_shows_on_the_student_fees_page(self):
        w = Waiver.objects.create(invoice=self.invoice, amount=Decimal("150.00"),
                                  reason="Bursary", recorded_by=self.bursar)
        self.client.force_login(self.bursar)
        resp = self.client.get(reverse("student_detail", args=[self.student.pk]))
        self.assertContains(resp, "Waived")
        self.assertContains(resp, w.note_no)
        self.assertContains(resp, reverse("waive", args=[self.invoice.pk]))
        self.client.force_login(self.lecturer)
        resp = self.client.get(reverse("student_detail", args=[self.student.pk]))
        self.assertNotContains(resp, reverse("waive", args=[self.invoice.pk]))


class WaiverPaymentInteractionTests(BaseWaivers):
    def test_payment_after_a_waiver_respects_the_net_balance(self):
        Waiver.objects.create(invoice=self.invoice, amount=Decimal("300.00"),
                              reason="Bursary", recorded_by=self.bursar)
        self.client.force_login(self.bursar)
        resp = self.client.post(reverse("record_payment", args=[self.student.pk]),
                                {"invoice": self.invoice.pk, "amount": "100.00",
                                 "method": "cash"}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Payment.objects.count(), 2)
        self.assertEqual(self.invoice.balance, Decimal("0"))
        resp = self.client.post(reverse("record_payment", args=[self.student.pk]),
                                {"invoice": self.invoice.pk, "amount": "0.01",
                                 "method": "cash"})
        self.assertIn("exceeds", str(resp.context["form"].errors))


class DebtorsWithWaiverTests(BaseWaivers):
    def test_debtors_report_shows_waived_amounts(self):
        Waiver.objects.create(invoice=self.invoice, amount=Decimal("150.00"),
                              reason="Bursary", recorded_by=self.bursar)
        self.client.force_login(self.bursar)
        resp = self.client.get(reverse("report_debtors"))
        row = resp.context["rows"][0]
        self.assertEqual(row.billed_amt, Decimal("500.00"))
        self.assertEqual(row.paid_amt, Decimal("100.00"))
        self.assertEqual(row.waived_amt, Decimal("150.00"))
        self.assertEqual(row.owed, Decimal("250.00"))
        self.assertEqual(resp.context["totals"]["waived"], Decimal("150.00"))
        self.assertEqual(resp.context["totals"]["owed"], Decimal("250.00"))
        aging = dict(resp.context["aging"])
        self.assertEqual(aging["1–30 days overdue"], Decimal("250.00"))

    def test_fully_waived_student_is_not_shown_as_owing(self):
        Waiver.objects.create(invoice=self.invoice, amount=Decimal("400.00"),
                              reason="Full write-off", recorded_by=self.bursar)
        self.client.force_login(self.bursar)
        resp = self.client.get(reverse("report_debtors"))
        self.assertEqual(len(resp.context["rows"]), 0)
        resp = self.client.get(reverse("report_debtors") + "?show=paid")
        self.assertEqual(len(resp.context["rows"]), 1)
        self.assertEqual(resp.context["rows"][0].owed, Decimal("0"))


class StudentPortalWithWaiverTests(BaseWaivers):
    def test_student_portal_shows_waived_marker(self):
        Waiver.objects.create(invoice=self.invoice, amount=Decimal("100.00"),
                              reason="Bursary", recorded_by=self.bursar)
        self.client.force_login(self.student_user)
        resp = self.client.get(reverse("student_portal"))
        self.assertContains(resp, "$100 waived")
        self.assertContains(resp, "$300")
