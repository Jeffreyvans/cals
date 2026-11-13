import csv
import io
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from academics.models import Programme, Student
from finance import services
from finance.models import CollectionTarget, Invoice, Payment

User = get_user_model()


class FinanceTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            "target-admin", password="pw123456", role="admin",
            is_staff=True, is_superuser=True)
        cls.bursar = User.objects.create_user(
            "target-bursar", password="pw123456", role="bursar")
        cls.lecturer = User.objects.create_user(
            "target-lecturer", password="pw123456", role="lecturer")
        cls.programme = Programme.objects.create(
            name="Diploma in IT", code="DIT", level="diploma")
        cls.student_user = User.objects.create_user(
            "target-student", password="pw123456", role="student")
        cls.student = Student.objects.create(
            user=cls.student_user, programme=cls.programme,
            student_no="T9001")
        student = cls.student
        invoice = Invoice.objects.create(
            student=student, description="Tuition", amount=Decimal("500.00"),
            due_date=timezone.localdate())
        cls.payment = Payment.objects.create(
            invoice=invoice, amount=Decimal("250.00"))
        cls.today = timezone.localdate()

    def make_target(self, amount="1000.00", start=None, end=None, **extra):
        return CollectionTarget.objects.create(
            name=extra.pop("name", "Term 1"),
            start_date=start or self.today - timedelta(days=1),
            end_date=end or self.today + timedelta(days=1),
            amount=Decimal(amount), **extra)

    def rows_of(self, response):
        return list(csv.reader(
            io.StringIO(response.content.decode("utf-8-sig"))))


class CollectionTargetTests(FinanceTestBase):
    def test_rows_compare_collections_in_the_period(self):
        target = self.make_target()
        rows = services.collection_target_rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["target"], target)
        self.assertEqual(row["collected"], Decimal("250.00"))
        self.assertEqual(row["variance"], Decimal("-750.00"))
        self.assertEqual(row["percent"], Decimal("25.0"))

    def test_payments_outside_the_period_are_ignored(self):
        Payment.objects.update(
            paid_on=timezone.now() - timedelta(days=30))
        self.make_target(start=self.today - timedelta(days=5),
                         end=self.today + timedelta(days=5))
        row = services.collection_target_rows()[0]
        self.assertEqual(row["collected"], Decimal("0.00"))
        self.assertEqual(row["percent"], Decimal("0.0"))

    def test_as_of_caps_late_payments(self):
        self.make_target()
        row = services.collection_target_rows(
            as_of=self.today - timedelta(days=1))[0]
        self.assertEqual(row["collected"], Decimal("0.00"))

    def test_report_lists_targets_and_links(self):
        target = self.make_target()
        self.client.force_login(self.bursar)
        response = self.client.get(reverse("collection_targets"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, target.name)
        self.assertContains(response, reverse("export_collection_targets"))
        self.assertContains(response, reverse("collection_target_create"))

    def test_lecturer_is_refused(self):
        self.client.force_login(self.lecturer)
        self.assertEqual(
            self.client.get(reverse("collection_targets")).status_code, 403)

    def test_admin_can_create_and_delete_a_target(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("collection_target_create"), {
            "name": "Year target",
            "start_date": self.today.isoformat(),
            "end_date": (self.today + timedelta(days=30)).isoformat(),
            "amount": "5000.00",
        })
        self.assertEqual(response.status_code, 302)
        target = CollectionTarget.objects.get(name="Year target")
        self.assertEqual(target.created_by, self.admin)
        response = self.client.post(
            reverse("collection_target_delete", args=[target.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(CollectionTarget.objects.filter(pk=target.pk).exists())

    def test_target_cannot_end_before_it_starts(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("collection_target_create"), {
            "name": "Backwards",
            "start_date": self.today.isoformat(),
            "end_date": (self.today - timedelta(days=1)).isoformat(),
            "amount": "100.00",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "must end on or after it starts")

    def test_export_has_each_target_and_total_columns(self):
        self.make_target()
        self.client.force_login(self.bursar)
        response = self.client.get(reverse("export_collection_targets"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("collection-targets-", response["Content-Disposition"])
        rows = self.rows_of(response)
        self.assertEqual(rows[0], ["Target", "Department", "Year", "From",
                                   "To", "Goal", "Collected", "Variance",
                                   "Percent"])
        self.assertEqual(rows[1][0], "Term 1")
        self.assertEqual(rows[1][5:9],
                         ["1000.00", "250.00", "-750.00", "25.0"])


class FinanceExportMatrixTests(FinanceTestBase):
    def test_each_report_exports_csv(self):
        self.make_target()
        self.client.force_login(self.bursar)
        names = [
            "export_trial_balance", "export_income_statement",
            "export_balance_sheet", "export_income_by_item",
            "export_refunds", "export_expenses", "export_cashup",
            "export_budgets",
        ]
        for name in names:
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], "text/csv")
                self.assertIn("attachment", response["Content-Disposition"])
                self.assertTrue(self.rows_of(response))

    def test_each_report_can_export_excel_and_pdf(self):
        self.client.force_login(self.bursar)
        for name in ("export_trial_balance", "export_collections"):
            for fmt, content_type, start in (
                    ("xlsx", "spreadsheetml", b"PK"),
                    ("pdf", "application/pdf", b"%PDF")):
                with self.subTest(name=name, fmt=fmt):
                    response = self.client.get(
                        reverse(name) + f"?format={fmt}")
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(content_type, response["Content-Type"])
                    self.assertTrue(response.content.startswith(start))

    def test_unknown_format_falls_back_to_csv(self):
        self.client.force_login(self.bursar)
        response = self.client.get(reverse("export_trial_balance")
                                   + "?format=docx")
        self.assertEqual(response["Content-Type"], "text/csv")

    def test_report_pages_link_to_their_exports(self):
        self.client.force_login(self.bursar)
        pairs = [
            ("trial_balance", "export_trial_balance"),
            ("income_statement", "export_income_statement"),
            ("balance_sheet", "export_balance_sheet"),
            ("report_income_by_item", "export_income_by_item"),
            ("report_refunds", "export_refunds"),
            ("report_expenses", "export_expenses"),
            ("report_cashup", "export_cashup"),
            ("budget_list", "export_budgets"),
        ]
        for page, export in pairs:
            with self.subTest(page=page):
                response = self.client.get(reverse(page))
                self.assertContains(response, reverse(export))
