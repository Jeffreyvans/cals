import csv
import io
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse

from finance.models import Invoice, MoneyLog, Payment, Waiver
from .test_audit import BaseAudit

User = get_user_model()


def csv_rows(resp):
    text = resp.content.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


class AuditDateFilterTests(BaseAudit):
    def setUp(self):
        super().setUp()
        self.payment = Payment.objects.create(invoice=self.invoice,
                                              amount=Decimal("150.00"),
                                              received_by=self.bursar)
        # push the invoice entry into the past; payment stays today
        MoneyLog.objects.filter(action="invoice").update(
            created=date.today() - timedelta(days=60))
        self.client.force_login(self.bursar)

    def test_from_date_hides_older_entries(self):
        resp = self.client.get(reverse("audit_log"),
                               {"from": (date.today()
                                         - timedelta(days=10)).isoformat()})
        entries = list(resp.context["entries"])
        self.assertEqual([e.action for e in entries], ["payment"])

    def test_to_date_hides_newer_entries(self):
        resp = self.client.get(reverse("audit_log"),
                               {"to": (date.today()
                                       - timedelta(days=30)).isoformat()})
        entries = list(resp.context["entries"])
        self.assertEqual([e.action for e in entries], ["invoice"])

    def test_bad_date_is_reported_and_ignored(self):
        resp = self.client.get(reverse("audit_log"), {"from": "banana"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "is not a date")
        self.assertEqual(len(resp.context["entries"]), 2)   # nothing filtered

    def test_from_after_to_is_reported_and_reset(self):
        resp = self.client.get(reverse("audit_log"),
                               {"from": date.today().isoformat(),
                                "to": (date.today()
                                       - timedelta(days=1)).isoformat()})
        self.assertContains(resp, "The from date is after the to date.")
        self.assertEqual(len(resp.context["entries"]), 2)   # filters dropped

    def test_action_chip_keeps_the_dates(self):
        resp = self.client.get(reverse("audit_log"),
                               {"action": "payment",
                                "from": (date.today()
                                         - timedelta(days=10)).isoformat()})
        self.assertContains(resp, "action=payment&amp;from=")
        # Apply form keeps the active action in a hidden field
        self.assertContains(resp, 'name="action" value="payment"')


class AuditExportTests(BaseAudit):
    def setUp(self):
        super().setUp()
        self.payment = Payment.objects.create(invoice=self.invoice,
                                              amount=Decimal("150.00"),
                                              received_by=self.bursar)
        self.url = reverse("audit_export")

    def test_bursar_downloads_a_csv_of_the_whole_trail(self):
        self.client.force_login(self.bursar)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/csv")
        self.assertIn("audit-log-", resp["Content-Disposition"])
        self.assertTrue(resp.content.startswith("\ufeff".encode("utf-8")))
        rows = csv_rows(resp)
        self.assertEqual(rows[0], ["When", "Who", "Action", "Details",
                                   "Student", "Student no", "Reference",
                                   "Amount"])
        body = "\n".join(",".join(r) for r in rows)
        self.assertIn(self.payment.receipt_no, body)
        self.assertIn("Invoice issued", body)
        self.assertIn("Payment received", body)
        self.assertEqual(rows[-1][0], "TOTAL")
        self.assertEqual(rows[-1][7], "650.00")   # 500 invoice + 150 payment

    def test_action_filter_applies_to_the_export(self):
        self.client.force_login(self.bursar)
        rows = csv_rows(self.client.get(self.url, {"action": "payment"}))
        self.assertTrue(all(r[2] == "Payment received"
                            for r in rows[1:-1]))
        self.assertNotIn("Invoice issued",
                         "\n".join(",".join(r) for r in rows))

    def test_date_filter_applies_to_the_export(self):
        self.client.force_login(self.bursar)
        rows = csv_rows(self.client.get(self.url, {"from": "2099-01-01"}))
        self.assertEqual(len(rows), 2)              # header + TOTAL
        self.assertEqual(rows[-1][7], "0.00")

    def test_registrar_is_refused_admin_role_allowed(self):
        self.client.force_login(self.registrar)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url).status_code, 200)


class AdminAuditTests(BaseAudit):
    """Edits and deletions made in the Django admin join the trail."""

    def setUp(self):
        super().setUp()
        self.root = User.objects.create_user(
            "root", password="pw123456", role="admin",
            is_staff=True, is_superuser=True)
        self.client.force_login(self.root)

    def test_payment_amount_edit_logs_an_adjustment(self):
        p = Payment.objects.create(invoice=self.invoice,
                                   amount=Decimal("150.00"),
                                   received_by=self.bursar)
        url = reverse("admin:finance_payment_change", args=[p.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        before = MoneyLog.objects.count()
        resp = self.client.post(url, {
            "invoice": str(self.invoice.pk), "amount": "120.00",
            "method": "cash", "reference": "",
            "received_by": str(self.bursar.pk)})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(MoneyLog.objects.count(), before + 1)
        entry = MoneyLog.objects.get(action="adjust")
        self.assertEqual(entry.actor, self.root)
        self.assertEqual(entry.reference, p.receipt_no)
        self.assertEqual(entry.amount, Decimal("120.00"))
        self.assertIn("150.00", entry.description)
        self.assertIn("120.00", entry.description)
        p.refresh_from_db()
        self.assertEqual(p.amount, Decimal("120.00"))

    def test_payment_saved_without_changes_logs_nothing(self):
        p = Payment.objects.create(invoice=self.invoice,
                                   amount=Decimal("150.00"),
                                   received_by=self.bursar)
        before = MoneyLog.objects.count()
        resp = self.client.post(reverse("admin:finance_payment_change",
                                        args=[p.pk]), {
            "invoice": str(self.invoice.pk), "amount": "150.00",
            "method": "cash", "reference": "",
            "received_by": str(self.bursar.pk)})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(MoneyLog.objects.count(), before)

    def test_invoice_amount_edit_logs_an_adjustment(self):
        url = reverse("admin:finance_invoice_change", args=[self.invoice.pk])
        resp = self.client.post(url, {
            "student": str(self.student.pk),
            "description": self.invoice.description,
            "amount": "550.00",
            "due_date": self.invoice.due_date.isoformat()})
        self.assertEqual(resp.status_code, 302)
        entry = MoneyLog.objects.get(action="adjust")
        self.assertEqual(entry.actor, self.root)
        self.assertIn("500.00", entry.description)
        self.assertIn("550.00", entry.description)

    def test_waiver_edits_log_adjustments(self):
        w = Waiver.objects.create(invoice=self.invoice,
                                  amount=Decimal("50.00"),
                                  reason="Bursary", recorded_by=self.bursar)
        url = reverse("admin:finance_waiver_change", args=[w.pk])
        self.client.post(url, {
            "invoice": str(self.invoice.pk), "amount": "40.00",
            "reason": "Bursary", "recorded_by": str(self.bursar.pk)})
        entry = MoneyLog.objects.filter(action="adjust").latest("pk")
        self.assertIn("50.00 -> 40.00", entry.description)
        self.assertEqual(entry.reference, w.note_no)

        self.client.post(url, {
            "invoice": str(self.invoice.pk), "amount": "40.00",
            "reason": "Hardship", "recorded_by": str(self.bursar.pk)})
        entry = MoneyLog.objects.filter(action="adjust").latest("pk")
        self.assertIn("reason changed from 'Bursary'", entry.description)

    def test_payment_deleted_in_admin_logs_a_removal(self):
        p = Payment.objects.create(invoice=self.invoice,
                                   amount=Decimal("150.00"),
                                   received_by=self.bursar)
        url = reverse("admin:finance_payment_delete", args=[p.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        resp = self.client.post(url, {"post": "yes"})
        self.assertEqual(resp.status_code, 302)
        entry = MoneyLog.objects.get(action="remove")
        self.assertEqual(entry.actor, self.root)
        self.assertEqual(entry.reference, p.receipt_no)
        self.assertEqual(entry.amount, Decimal("150.00"))
        self.assertIn("Deleted payment against 'Tuition'",
                      entry.description)
        self.assertEqual(entry.invoice, self.invoice)
        self.assertFalse(Payment.objects.filter(pk=p.pk).exists())

    def test_invoice_deleted_in_admin_logs_cascades(self):
        Payment.objects.create(invoice=self.invoice,
                               amount=Decimal("150.00"),
                               received_by=self.bursar)
        Waiver.objects.create(invoice=self.invoice,
                              amount=Decimal("50.00"),
                              reason="Bursary", recorded_by=self.bursar)
        url = reverse("admin:finance_invoice_delete", args=[self.invoice.pk])
        resp = self.client.post(url, {"post": "yes"})
        self.assertEqual(resp.status_code, 302)
        entry = MoneyLog.objects.get(action="remove")
        self.assertIn("Deleted invoice 'Tuition'", entry.description)
        self.assertIn("1 payment", entry.description)
        self.assertIn("1 credit note", entry.description)
        self.assertIsNone(entry.invoice)          # FK nulled by the cascade
        self.assertEqual(entry.student, self.student)
        self.assertFalse(Invoice.objects.filter(pk=self.invoice.pk).exists())

    def test_invoice_created_in_admin_logs_once_with_the_actor(self):
        before = MoneyLog.objects.count()
        resp = self.client.post(reverse("admin:finance_invoice_add"), {
            "student": str(self.student.pk),
            "description": "Admin raised fee",
            "amount": "10.00",
            "due_date": (date.today() + timedelta(days=10)).isoformat()})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(MoneyLog.objects.count(), before + 1)
        entry = MoneyLog.objects.filter(action="invoice").latest("pk")
        self.assertEqual(entry.actor, self.root)
        self.assertEqual(entry.description, "Admin raised fee")

    def test_moneylog_admin_is_read_only(self):
        entry = MoneyLog.objects.filter(action="invoice").first()
        self.assertEqual(
            self.client.get(reverse("admin:finance_moneylog_changelist"))
            .status_code, 200)
        self.assertEqual(
            self.client.get(reverse("admin:finance_moneylog_add")).status_code,
            403)
        # view permission still allows a read-only look...
        self.assertEqual(
            self.client.get(reverse("admin:finance_moneylog_change",
                                    args=[entry.pk])).status_code, 200)
        # ...but saving or deleting is refused
        self.assertEqual(
            self.client.post(reverse("admin:finance_moneylog_change",
                                     args=[entry.pk]),
                             {"action": "invoice"}).status_code, 403)
        self.assertEqual(
            self.client.get(reverse("admin:finance_moneylog_delete",
                                    args=[entry.pk])).status_code, 403)
