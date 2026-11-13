from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from academics.factories import ProgrammeFactory, StudentFactory
from accounts.factories import UserFactory
from core.models import Institution, OutboundMessage, StaffProfile
from core.modules import set_module
from core.roles import ensure_roles
from core.tenancy import use_institution
from operations.models import (
    LeaveRequest,
    Newsletter,
    PayrollRule,
    PayrollRun,
    Policy,
    PolicyAcknowledgement,
    SalaryStructure,
    StockItem,
    Vacancy,
)
from operations.registry import MODULES
from operations.services import generate_payslips


class OperationsViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.institution = Institution.objects.get(slug="default")
        ensure_roles(cls.institution)
        for code, info in MODULES.items():
            set_module(cls.institution, info.get("flag", code), True)
        cls.admin = UserFactory(
            username="ops_view_admin", role="admin", institution=cls.institution
        )
        cls.staff_user = UserFactory(
            username="ops_view_staff", role="lecturer", institution=cls.institution
        )
        cls.staff = StaffProfile.objects.create(
            institution=cls.institution,
            user=cls.staff_user,
            employee_no="EMP-100",
            job_title="Teacher",
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_a_vacancy_can_be_created_edited_and_removed(self):
        create = self.client.post(
            reverse("operations:create", args=["hr", "vacancies"]),
            {
                "title": "Mathematics teacher",
                "description": "A-level",
                "closes_on": "2026-03-01",
                "status": "open",
            },
        )
        self.assertEqual(create.status_code, 302)
        vacancy = Vacancy.objects.get(title="Mathematics teacher")
        edit = self.client.post(
            reverse("operations:edit", args=["hr", "vacancies", vacancy.pk]),
            {
                "title": "Senior mathematics teacher",
                "description": "A-level",
                "closes_on": "2026-03-15",
                "status": "open",
            },
        )
        self.assertEqual(edit.status_code, 302)
        vacancy.refresh_from_db()
        self.assertEqual(vacancy.title, "Senior mathematics teacher")
        delete = self.client.post(
            reverse("operations:delete", args=["hr", "vacancies", vacancy.pk])
        )
        self.assertEqual(delete.status_code, 302)
        self.assertFalse(Vacancy.objects.filter(pk=vacancy.pk).exists())

    def test_the_register_search_filters_rows(self):
        Vacancy.objects.create(institution=self.institution, title="History teacher")
        Vacancy.objects.create(institution=self.institution, title="Lab technician")
        response = self.client.get(
            reverse("operations:list", args=["hr", "vacancies"]), {"q": "History"}
        )
        self.assertContains(response, "History teacher")
        self.assertNotContains(response, "Lab technician")

    def test_a_record_from_another_institution_is_not_visible(self):
        other = Institution.objects.create(
            name="Other School", slug="other-school", type="secondary"
        )
        with use_institution(other):
            hidden = Vacancy.all_objects.create(institution=other, title="Other school vacancy")
        response = self.client.get(reverse("operations:list", args=["hr", "vacancies"]))
        self.assertNotContains(response, "Other school vacancy")
        response = self.client.get(reverse("operations:edit", args=["hr", "vacancies", hidden.pk]))
        self.assertEqual(response.status_code, 404)

    def test_the_actor_is_recorded_when_the_form_leaves_it_out(self):
        response = self.client.post(
            reverse("operations:create", args=["estates", "work-orders"]),
            {
                "title": "Fix the hall door",
                "location": "Main hall",
                "category": "Carpentry",
                "priority": "high",
                "status": "open",
                "description": "",
                "assigned_to": "",
                "opened_on": "2026-02-01",
                "due_on": "",
                "completed_on": "",
                "cost": "0",
            },
        )
        self.assertEqual(response.status_code, 302)
        from operations.models import WorkOrder

        work_order = WorkOrder.objects.get(title="Fix the hall door")
        self.assertEqual(work_order.reported_by, self.admin)

    def test_stock_movements_update_the_stock_level(self):
        item = StockItem.objects.create(
            institution=self.institution,
            code="CHALK",
            name="Chalk",
            unit="box",
            quantity=Decimal("10"),
        )
        received = self.client.post(
            reverse("operations:create", args=["procurement", "stock-movements"]),
            {"item": item.pk, "kind": "in", "quantity": "5", "note": "Delivery"},
        )
        self.assertEqual(received.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.quantity, Decimal("15.00"))
        issued = self.client.post(
            reverse("operations:create", args=["procurement", "stock-movements"]),
            {"item": item.pk, "kind": "out", "quantity": "3", "note": "Class"},
        )
        self.assertEqual(issued.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.quantity, Decimal("12.00"))

    def test_an_hr_officer_can_decide_a_leave_request(self):
        hr = UserFactory(username="ops_view_hr", role="student", institution=self.institution)
        hr.primary_role = self.institution.roles.get(code="hr_officer")
        hr.save()
        leave = LeaveRequest.objects.create(
            institution=self.institution,
            staff=self.staff,
            start_date=date(2026, 4, 1),
            end_date=date(2026, 4, 2),
            reason="Family",
        )
        self.client.force_login(hr)
        page = self.client.get(reverse("operations:leave_decision", args=[leave.pk]))
        self.assertEqual(page.status_code, 200)
        response = self.client.post(
            reverse("operations:leave_decision", args=[leave.pk]),
            {"decision": "approve", "note": "Approved"},
        )
        self.assertEqual(response.status_code, 302)
        leave.refresh_from_db()
        self.assertEqual(leave.status, LeaveRequest.Status.APPROVED)

    def test_payroll_can_be_generated_approved_and_downloaded(self):
        PayrollRule.objects.create(
            institution=self.institution, kind=PayrollRule.Kind.TAX, name="PAYE", rate=Decimal("10")
        )
        SalaryStructure.objects.create(
            institution=self.institution,
            staff=self.staff,
            basic=Decimal("800"),
            housing_allowance=Decimal("100"),
            bank_name="CBZ",
            bank_account="123456",
        )
        run = PayrollRun.objects.create(
            institution=self.institution,
            name="March 2026",
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 31),
            pay_date=date(2026, 3, 25),
            created_by=self.admin,
        )
        detail = self.client.get(reverse("operations:payroll_run_detail", args=[run.pk]))
        self.assertEqual(detail.status_code, 200)
        generated = self.client.post(reverse("operations:payroll_run_generate", args=[run.pk]))
        self.assertEqual(generated.status_code, 302)
        self.assertEqual(run.payslips.count(), 1)
        payslip = run.payslips.get()
        pdf = self.client.get(reverse("operations:payroll_payslip_pdf", args=[payslip.pk]))
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        approved = self.client.post(reverse("operations:payroll_run_approve", args=[run.pk]))
        self.assertEqual(approved.status_code, 302)
        run.refresh_from_db()
        self.assertEqual(run.status, PayrollRun.Status.APPROVED)
        bank = self.client.get(reverse("operations:payroll_run_bank", args=[run.pk]))
        self.assertEqual(bank.status_code, 200)
        self.assertIn("text/csv", bank["Content-Type"])
        content = bank.content.decode()
        self.assertIn("CBZ", content)
        self.assertIn("123456", content)
        posted = self.client.post(reverse("operations:payroll_run_post", args=[run.pk]))
        self.assertEqual(posted.status_code, 302)
        run.refresh_from_db()
        self.assertEqual(run.status, PayrollRun.Status.PAID)

    def test_a_policy_can_be_acknowledged_once(self):
        policy = Policy.objects.create(
            institution=self.institution, code="SAF-01", title="Safety policy"
        )
        page = self.client.get(reverse("operations:policy_acknowledge", args=[policy.pk]))
        self.assertEqual(page.status_code, 200)
        first = self.client.post(reverse("operations:policy_acknowledge", args=[policy.pk]))
        self.assertEqual(first.status_code, 302)
        second = self.client.post(reverse("operations:policy_acknowledge", args=[policy.pk]))
        self.assertEqual(second.status_code, 302)
        self.assertEqual(
            PolicyAcknowledgement.objects.filter(policy=policy, user=self.admin).count(), 1
        )

    def test_a_newsletter_can_be_sent_from_the_confirmation_page(self):
        StudentFactory(
            user__email="ops_view_learner@example.com",
            status="active",
            programme=ProgrammeFactory(),
        )
        newsletter = Newsletter.objects.create(
            institution=self.institution,
            title="April news",
            body="The April newsletter.",
            created_by=self.admin,
        )
        page = self.client.get(reverse("operations:newsletter_send", args=[newsletter.pk]))
        self.assertEqual(page.status_code, 200)
        response = self.client.post(reverse("operations:newsletter_send", args=[newsletter.pk]))
        self.assertEqual(response.status_code, 302)
        newsletter.refresh_from_db()
        self.assertEqual(newsletter.status, Newsletter.Status.SENT)
        self.assertTrue(
            OutboundMessage.objects.filter(recipient="ops_view_learner@example.com").exists()
        )

    def test_generate_is_a_no_op_after_the_payslips_exist(self):
        SalaryStructure.objects.create(
            institution=self.institution, staff=self.staff, basic=Decimal("400")
        )
        run = PayrollRun.objects.create(
            institution=self.institution,
            name="April 2026",
            start_date=date(2026, 4, 1),
            end_date=date(2026, 4, 30),
            pay_date=date(2026, 4, 25),
            created_by=self.admin,
        )
        generate_payslips(run)
        response = self.client.post(reverse("operations:payroll_run_generate", args=[run.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(run.payslips.count(), 1)

