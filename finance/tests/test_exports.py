import csv
import io
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from academics.models import Programme, Student
from finance.models import Invoice, Payment, Waiver

User = get_user_model()


class BaseExports(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.bursar = User.objects.create_user("bur", password="pw123456",
                                              role="bursar")
        cls.registrar = User.objects.create_user("reg", password="pw123456",
                                                 role="registrar")
        cls.lecturer = User.objects.create_user("lec", password="pw123456",
                                                role="lecturer")
        cls.programme = Programme.objects.create(name="Diploma in IT", code="DIT",
                                                 level="diploma")
        today = date.today()

        def mk(username, first, last):
            u = User.objects.create_user(username, password="pw123456",
                                         first_name=first, last_name=last,
                                         role="student")
            return Student.objects.create(user=u, programme=cls.programme)

        cls.alice = mk("alice", "Alice", "Adams")
        cls.bob = mk("bob", "Bob", "Banda")
        cls.carol = mk("carol", "Carol", "Chanda")
        inv_a = Invoice.objects.create(student=cls.alice, description="Tuition",
                                       amount=Decimal("100.00"),
                                       due_date=today - timedelta(days=40))
        cls.pay_a = Payment.objects.create(invoice=inv_a,
                                           amount=Decimal("100.00"))
        inv_b = Invoice.objects.create(student=cls.bob, description="Hostel",
                                       amount=Decimal("200.00"),
                                       due_date=today - timedelta(days=10))
        cls.pay_b = Payment.objects.create(invoice=inv_b,
                                           amount=Decimal("50.00"),
                                           method="mobile",
                                           reference="ECOCASH-1")
        Waiver.objects.create(invoice=inv_b, amount=Decimal("30.00"),
                              reason="Bursary", recorded_by=cls.bursar)
        cls.inv_c = Invoice.objects.create(student=cls.carol,
                                           description="Tuition",
                                           amount=Decimal("300.00"),
                                           due_date=today + timedelta(days=30))

    def rows_of(self, resp):
        return list(csv.reader(io.StringIO(resp.content.decode("utf-8-sig"))))


class DebtorsExportTests(BaseExports):
    def url(self, **params):
        url = reverse("export_debtors")
        if params:
            url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        return url

    def test_bursar_and_registrar_can_export(self):
        for user in (self.bursar, self.registrar):
            self.client.force_login(user)
            with self.subTest(user=user.username):
                resp = self.client.get(self.url())
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp["Content-Type"], "text/csv")
                self.assertIn("attachment", resp["Content-Disposition"])
                self.assertIn("debtors-", resp["Content-Disposition"])

    def test_lecturers_and_students_are_refused(self):
        for user in (self.lecturer, self.alice.user):
            self.client.force_login(user)
            with self.subTest(user=user.username):
                self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_default_export_lists_only_debtors(self):
        self.client.force_login(self.bursar)
        rows = self.rows_of(self.client.get(self.url()))
        self.assertEqual(rows[0], ["Student", "Student no", "Programme", "Class",
                                   "Billed", "Received", "Waived", "Owed"])
        names = [r[0] for r in rows[1:-1]]
        self.assertEqual(names, ["Bob Banda", "Carol Chanda"])
        bob = rows[1]
        self.assertEqual(bob[4:8], ["200.00", "50.00", "30.00", "120.00"])
        self.assertEqual(rows[-1][0], "TOTAL")
        self.assertEqual(rows[-1][4:8], ["500.00", "50.00", "30.00", "420.00"])

    def test_show_all_includes_settled_students(self):
        self.client.force_login(self.bursar)
        rows = self.rows_of(self.client.get(self.url(show="all")))
        names = [r[0] for r in rows[1:-1]]
        self.assertIn("Alice Adams", names)
        alice = rows[names.index("Alice Adams") + 1]
        self.assertEqual(alice[4:8], ["100.00", "100.00", "0.00", "0.00"])

    def test_unknown_filter_falls_back_to_owing(self):
        self.client.force_login(self.bursar)
        rows = self.rows_of(self.client.get(self.url(show="bogus")))
        names = [r[0] for r in rows[1:-1]]
        self.assertNotIn("Alice Adams", names)

    def test_report_page_links_to_the_export(self):
        self.client.force_login(self.bursar)
        resp = self.client.get(reverse("report_debtors"))
        self.assertContains(resp, reverse("export_debtors"))


class CollectionsExportTests(BaseExports):
    def url(self, **params):
        url = reverse("export_collections")
        if params:
            url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        return url

    def test_bursar_and_registrar_can_export(self):
        for user in (self.bursar, self.registrar):
            self.client.force_login(user)
            with self.subTest(user=user.username):
                resp = self.client.get(self.url())
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp["Content-Type"], "text/csv")
                self.assertIn("collections-", resp["Content-Disposition"])

    def test_lecturers_and_students_are_refused(self):
        for user in (self.lecturer, self.alice.user):
            self.client.force_login(user)
            with self.subTest(user=user.username):
                self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_every_receipt_with_method_breakdown_and_total(self):
        self.client.force_login(self.bursar)
        rows = self.rows_of(self.client.get(self.url()))
        self.assertEqual(rows[0], ["Receipt", "Date", "Student", "Student no",
                                   "For", "Method", "Reference", "Amount",
                                   "Received by"])
        receipts = [r for r in rows if r and r[0].startswith("RC")]
        self.assertEqual(len(receipts), 2)
        bob = next(r for r in receipts if r[2] == "Bob Banda")
        self.assertEqual(bob[4:8], ["Hostel", "Mobile money", "ECOCASH-1",
                                    "50.00"])
        self.assertIn(["Cash", "", "", "", "", "", "", "100.00"], rows)
        self.assertIn(["Mobile money", "", "", "", "", "", "", "50.00"], rows)
        total = next(r for r in rows if r and r[0] == "TOTAL")
        self.assertEqual(total[7], "150.00")

    def test_date_filters_narrow_the_export(self):
        today = date.today()
        self.client.force_login(self.bursar)
        today_only = self.client.get(self.url(**{"from": today.isoformat(),
                                                 "to": today.isoformat()}))
        receipts = [r for r in self.rows_of(today_only)
                    if r and r[0].startswith("RC")]
        self.assertEqual(len(receipts), 2)
        tomorrow = today + timedelta(days=1)
        empty = self.client.get(self.url(**{"from": tomorrow.isoformat(),
                                            "to": tomorrow.isoformat()}))
        receipts = [r for r in self.rows_of(empty)
                    if r and r[0].startswith("RC")]
        self.assertEqual(len(receipts), 0)

    def test_bad_date_falls_back_to_everything(self):
        self.client.force_login(self.bursar)
        rows = self.rows_of(self.client.get(self.url(**{"from": "oops"})))
        receipts = [r for r in rows if r and r[0].startswith("RC")]
        self.assertEqual(len(receipts), 2)

    def test_report_page_links_to_the_export(self):
        self.client.force_login(self.bursar)
        resp = self.client.get(reverse("report_collections"))
        self.assertContains(resp, reverse("export_collections"))
