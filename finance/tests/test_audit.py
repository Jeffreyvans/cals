from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from academics.models import Programme, Student
from finance.models import Invoice, MoneyLog, Payment, Waiver

User = get_user_model()


class BaseAudit(TestCase):
    def setUp(self):
        self.bursar = User.objects.create_user("bur", password="pw123456",
                                               role="bursar")
        self.admin = User.objects.create_user("adm", password="pw123456",
                                              role="admin")
        self.registrar = User.objects.create_user("reg", password="pw123456",
                                                  role="registrar")
        self.lecturer = User.objects.create_user("lec", password="pw123456",
                                                 role="lecturer")
        self.student_user = User.objects.create_user("alice",
                                                     password="pw123456",
                                                     role="student")
        programme = Programme.objects.create(name="Diploma in IT", code="DIT",
                                             level="diploma")
        self.student = Student.objects.create(user=self.student_user,
                                              programme=programme,
                                              student_no="S0601")
        self.invoice = Invoice.objects.create(student=self.student,
                                              description="Tuition",
                                              amount=Decimal("500.00"),
                                              due_date=date.today(),
                                              created_by=self.bursar)


class MoneyLogEntryTests(BaseAudit):
    def setUp(self):
        super().setUp()
        MoneyLog.objects.all().delete()   # keep only what these tests create

    def test_invoice_creation_is_logged_with_the_issuer(self):
        inv = Invoice.objects.create(student=self.student, description="Hostel",
                                     amount=Decimal("200.00"),
                                     due_date=date.today(),
                                     created_by=self.registrar)
        entry = MoneyLog.objects.get(action="invoice")
        self.assertEqual(entry.actor, self.registrar)
        self.assertEqual(entry.amount, Decimal("200.00"))
        self.assertEqual(entry.student, self.student)
        self.assertEqual(entry.invoice, inv)
        self.assertEqual(entry.description, "Hostel")

    def test_invoice_without_issuer_shows_the_office(self):
        Invoice.objects.create(student=self.student, description="Trip",
                               amount=Decimal("30.00"), due_date=date.today())
        entry = MoneyLog.objects.get(action="invoice")
        self.assertIsNone(entry.actor)
        self.assertEqual(entry.actor_name, "EduSphere office")

    def test_payment_is_logged_with_receipt_and_taker(self):
        p = Payment.objects.create(invoice=self.invoice,
                                   amount=Decimal("150.00"),
                                   received_by=self.bursar)
        entry = MoneyLog.objects.get(action="payment")
        self.assertEqual(entry.actor, self.bursar)
        self.assertEqual(entry.amount, Decimal("150.00"))
        self.assertEqual(entry.reference, p.receipt_no)
        self.assertEqual(entry.invoice, self.invoice)
        self.assertEqual(entry.student, self.student)
        self.assertEqual(entry.description, "Tuition")   # what was paid for

    def test_credit_note_is_logged_with_number_and_recorder(self):
        w = Waiver.objects.create(invoice=self.invoice,
                                  amount=Decimal("50.00"),
                                  reason="Bursary", recorded_by=self.bursar)
        entry = MoneyLog.objects.get(action="waiver")
        self.assertEqual(entry.actor, self.bursar)
        self.assertEqual(entry.reference, w.note_no)
        self.assertEqual(entry.amount, Decimal("50.00"))
        self.assertEqual(entry.description, "Bursary")

    def test_saving_again_does_not_log_twice(self):
        p = Payment.objects.create(invoice=self.invoice,
                                   amount=Decimal("10.00"))
        p.save()
        p.save(update_fields=["reference"])
        self.assertEqual(MoneyLog.objects.count(), 1)

    def test_issued_invoice_cannot_be_deleted(self):
        inv = Invoice.objects.create(student=self.student, description="Trip",
                                     amount=Decimal("30.00"),
                                     due_date=date.today())
        with self.assertRaises(ValidationError):
            inv.delete()
        entry = MoneyLog.objects.get(action="invoice")
        self.assertEqual(entry.invoice, inv)
        self.assertEqual(entry.student, self.student)
        self.assertEqual(entry.amount, Decimal("30.00"))
        self.assertTrue(Invoice.objects.filter(pk=inv.pk).exists())


class AuditLogViewTests(BaseAudit):
    def url(self, **params):
        url = reverse("audit_log")
        if params:
            url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        return url

    def test_bursar_and_admin_can_open_it(self):
        for user in (self.bursar, self.admin):
            self.client.force_login(user)
            with self.subTest(user=user.username):
                self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_everyone_else_is_refused(self):
        for user in (self.registrar, self.lecturer, self.student_user):
            self.client.force_login(user)
            with self.subTest(user=user.username):
                self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_lists_entries_newest_first_with_details(self):
        old = Payment.objects.create(invoice=self.invoice,
                                     amount=Decimal("10.00"),
                                     received_by=self.bursar)
        new = Waiver.objects.create(invoice=self.invoice,
                                    amount=Decimal("20.00"), reason="Bursary",
                                    recorded_by=self.bursar)
        self.client.force_login(self.bursar)
        resp = self.client.get(self.url())
        entries = list(resp.context["entries"])
        self.assertEqual([e.action for e in entries],
                         ["waiver", "payment", "invoice"])
        self.assertContains(resp, old.receipt_no)
        self.assertContains(resp, new.note_no)
        self.assertContains(resp, "20.00")
        self.assertContains(resp, "Credit note issued")
        self.assertContains(resp, "Bursary")            # details column
        self.assertContains(resp, "Tuition")

    def test_action_filter_narrows_the_list(self):
        Payment.objects.create(invoice=self.invoice,
                               amount=Decimal("10.00"))
        Waiver.objects.create(invoice=self.invoice, amount=Decimal("20.00"),
                              reason="Bursary")
        self.client.force_login(self.bursar)
        only_waivers = self.client.get(self.url(action="waiver"))
        self.assertEqual(len(only_waivers.context["entries"]), 1)
        self.assertEqual(only_waivers.context["entries"][0].action, "waiver")

    def test_unknown_action_falls_back_to_all(self):
        Payment.objects.create(invoice=self.invoice,
                               amount=Decimal("10.00"))
        self.client.force_login(self.bursar)
        resp = self.client.get(self.url(action="bogus"))
        self.assertEqual(len(resp.context["entries"]), 2)   # invoice + payment
        self.assertEqual(resp.context["action"], "")

    def test_nav_link_shows_for_bursar_but_not_registrar(self):
        # The menu now points at the institution-wide audit trail, which also
        # covers money. The finance-only log is one click away from there.
        self.client.force_login(self.bursar)
        resp = self.client.get(reverse("dashboard"))
        self.assertContains(resp, reverse("audit_trail"))
        self.client.force_login(self.registrar)
        resp = self.client.get(reverse("dashboard"))
        self.assertNotContains(resp, reverse("audit_trail"))
