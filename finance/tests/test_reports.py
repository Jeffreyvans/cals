from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from academics.models import Programme, Student
from finance.models import Invoice, Payment

User = get_user_model()


class BaseReports(TestCase):
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
        cls.dave = mk("dave", "Dave", "Dube")
        cls.eve = mk("eve", "Eve", "Evans")
        inv_a = Invoice.objects.create(student=cls.alice, description="Tuition",
                                       amount=Decimal("100.00"),
                                       due_date=today - timedelta(days=40))
        Payment.objects.create(invoice=inv_a, amount=Decimal("100.00"))
        inv_b = Invoice.objects.create(student=cls.bob, description="Tuition",
                                       amount=Decimal("200.00"),
                                       due_date=today - timedelta(days=10))
        Payment.objects.create(invoice=inv_b, amount=Decimal("50.00"),
                               method="mobile")
        Invoice.objects.create(student=cls.carol, description="Tuition",
                               amount=Decimal("300.00"),
                               due_date=today + timedelta(days=30))
        Invoice.objects.create(student=cls.dave, description="Tuition",
                               amount=Decimal("80.00"),
                               due_date=today - timedelta(days=45))


class DebtorsReportTests(BaseReports):
    def url(self, **params):
        url = reverse("report_debtors")
        if params:
            url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        return url

    def test_bursar_and_registrar_can_open_it(self):
        for user in (self.bursar, self.registrar):
            self.client.force_login(user)
            with self.subTest(user=user.username):
                self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_others_are_refused(self):
        for user in (self.lecturer, self.alice.user):
            self.client.force_login(user)
            with self.subTest(user=user.username):
                self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_default_lists_only_students_owing(self):
        self.client.force_login(self.bursar)
        resp = self.client.get(self.url())
        names = {r.user.username for r in resp.context["rows"]}
        self.assertEqual(names, {"bob", "carol", "dave"})
        self.assertContains(resp, "Bob Banda")
        self.assertNotContains(resp, "Alice Adams")
        self.assertNotContains(resp, "Eve Evans")
        self.assertEqual(resp.context["totals"]["owed"], Decimal("530.00"))

    def test_everyone_billed_includes_settled_but_never_the_unbilled(self):
        self.client.force_login(self.bursar)
        resp = self.client.get(self.url(show="all"))
        names = {r.user.username for r in resp.context["rows"]}
        self.assertEqual(names, {"alice", "bob", "carol", "dave"})

    def test_fully_paid_lists_only_settled_students(self):
        self.client.force_login(self.bursar)
        resp = self.client.get(self.url(show="paid"))
        rows = list(resp.context["rows"])
        self.assertEqual([r.user.username for r in rows], ["alice"])
        self.assertEqual(rows[0].owed, Decimal("0.00"))

    def test_unknown_filter_falls_back_to_owing(self):
        self.client.force_login(self.bursar)
        resp = self.client.get(self.url(show="nonsense"))
        self.assertEqual(resp.context["show"], "owing")

    def test_aging_buckets_the_unpaid_invoices(self):
        self.client.force_login(self.bursar)
        resp = self.client.get(self.url())
        aging = dict(resp.context["aging"])
        self.assertEqual(aging["Not yet due"], Decimal("300.00"))
        self.assertEqual(aging["1–30 days overdue"], Decimal("150.00"))
        self.assertEqual(aging["31–60 days overdue"], Decimal("80.00"))
        self.assertEqual(aging["61+ days overdue"], Decimal("0.00"))

    def test_aging_ignores_invoices_already_settled(self):
        inv = Invoice.objects.create(student=self.alice, description="Trip",
                                     amount=Decimal("40.00"),
                                     due_date=date.today() - timedelta(days=90))
        Payment.objects.create(invoice=inv, amount=Decimal("40.00"))
        self.client.force_login(self.bursar)
        aging = dict(self.client.get(self.url()).context["aging"])
        self.assertEqual(aging["61+ days overdue"], Decimal("0.00"))

    def test_staff_nav_links_to_both_reports(self):
        self.client.force_login(self.bursar)
        resp = self.client.get(reverse("dashboard"))
        self.assertContains(resp, 'href="/finance/reports/debtors/"')
        self.assertContains(resp, 'href="/finance/reports/collections/"')


class CollectionsReportTests(BaseReports):
    def url(self, **params):
        url = reverse("report_collections")
        if params:
            url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        return url

    def test_bursar_and_registrar_can_open_it(self):
        for user in (self.bursar, self.registrar):
            self.client.force_login(user)
            with self.subTest(user=user.username):
                self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_lecturers_and_students_are_refused(self):
        for user in (self.lecturer, self.alice.user):
            self.client.force_login(user)
            with self.subTest(user=user.username):
                self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_default_lists_every_receipt_with_totals(self):
        self.client.force_login(self.bursar)
        resp = self.client.get(self.url())
        self.assertEqual(resp.context["total"], Decimal("150.00"))
        self.assertEqual(resp.context["student_count"], 2)
        self.assertEqual(len(resp.context["payments"]), 2)
        self.assertContains(resp, "Alice Adams")
        receipt_no = resp.context["payments"][-1].receipt_no
        self.assertContains(resp, receipt_no)

    def test_method_breakdown(self):
        self.client.force_login(self.bursar)
        resp = self.client.get(self.url())
        self.assertEqual(dict(resp.context["methods"]),
                         {"Cash": Decimal("100.00"), "Mobile money": Decimal("50.00"),
                          "Bank transfer": Decimal("0.00"), "Card": Decimal("0.00")})

    def test_date_filters_narrow_the_period(self):
        today = date.today()
        self.client.force_login(self.bursar)
        today_only = self.client.get(self.url(**{"from": today.isoformat(),
                                                 "to": today.isoformat()}))
        self.assertEqual(len(today_only.context["payments"]), 2)
        tomorrow = self.client.get(self.url(**{"from": (today + timedelta(days=1)).isoformat()}))
        self.assertEqual(tomorrow.context["total"], Decimal("0.00"))
        self.assertContains(tomorrow, "No payments in this period")
        yesterday = self.client.get(self.url(**{"to": (today - timedelta(days=1)).isoformat()}))
        self.assertEqual(len(yesterday.context["payments"]), 0)

    def test_bad_date_shows_an_error_and_keeps_all_payments(self):
        self.client.force_login(self.bursar)
        resp = self.client.get(self.url(**{"from": "banana"}), follow=True)
        self.assertContains(resp, "is not a date")
        self.assertEqual(len(resp.context["payments"]), 2)

    def test_reversed_range_is_refused(self):
        self.client.force_login(self.bursar)
        resp = self.client.get(self.url(**{"from": "2026-12-31", "to": "2026-01-01"}),
                               follow=True)
        self.assertContains(resp, "after the to date")
        self.assertEqual(len(resp.context["payments"]), 2)

    def test_receipts_link_to_the_receipt_page(self):
        self.client.force_login(self.bursar)
        resp = self.client.get(self.url())
        p = resp.context["payments"][0]
        self.assertContains(resp, f'href="/finance/receipt/{p.pk}/"')
