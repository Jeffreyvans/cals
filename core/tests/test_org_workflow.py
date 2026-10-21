"""Phase 1: departments, staff profiles and the approval workflow screens."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import (
    ApprovalAction,
    ApprovalRequest,
    Department,
    Institution,
    Role,
    StaffProfile,
    WorkflowTemplate,
)
from core.roles import ensure_roles
from core.tenancy import use_institution
from core.workflow import approve, can_act, reject, submit

User = get_user_model()


class DepartmentScreenTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.inst = Institution.objects.create(
            name="Alpha School", slug="alpha", type="secondary")
        ensure_roles(cls.inst)
        cls.admin = User.objects.create_user(
            "boss", password="pw123456", role="admin", is_superuser=True,
            institution=cls.inst)
        cls.teacher = User.objects.create_user(
            "teach", password="pw123456", role="lecturer", institution=cls.inst)

    def test_the_department_page_lists_the_departments(self):
        with use_institution(self.inst):
            Department.objects.create(institution=self.inst, name="Science", code="SCI")
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("department_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Science")

    def test_a_user_without_the_permission_is_refused(self):
        self.client.force_login(self.teacher)
        resp = self.client.get(reverse("department_list"))
        self.assertEqual(resp.status_code, 403)

    def test_a_department_can_be_created(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("department_create"), {
            "name": "Accounts", "code": "ACC",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            Department.all_objects.filter(institution=self.inst, code="ACC").exists()
        )

    def test_a_duplicate_code_is_refused(self):
        with use_institution(self.inst):
            Department.objects.create(institution=self.inst, name="Science", code="SCI")
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("department_create"), {
            "name": "Science again", "code": "SCI",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            Department.all_objects.filter(institution=self.inst, code="SCI").count(), 1
        )

    def test_a_department_from_another_institution_is_not_found(self):
        other = Institution.objects.create(name="Beta", slug="beta", type="secondary")
        with use_institution(other):
            foreign = Department.objects.create(
                institution=other, name="Foreign", code="FOR")
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("department_detail", args=[foreign.pk]))
        self.assertEqual(resp.status_code, 404)


class StaffProfileScreenTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.inst = Institution.objects.create(
            name="Alpha School", slug="alpha", type="secondary")
        ensure_roles(cls.inst)
        cls.admin = User.objects.create_user(
            "boss", password="pw123456", role="admin", is_superuser=True,
            institution=cls.inst)
        cls.teacher = User.objects.create_user(
            "teach", password="pw123456", role="lecturer", institution=cls.inst)

    def test_a_profile_is_created_from_the_staff_page(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("staff_detail", args=[self.teacher.pk]), {
            "job_title": "Head of Science", "employee_no": "EMP-01",
            "phone": "0770000001", "date_joined": "2026-01-15",
        })
        self.assertEqual(resp.status_code, 302)
        profile = StaffProfile.all_objects.get(user=self.teacher)
        self.assertEqual(profile.job_title, "Head of Science")
        self.assertEqual(profile.employee_no, "EMP-01")

    def test_a_profile_can_be_put_in_a_department(self):
        with use_institution(self.inst):
            dept = Department.objects.create(institution=self.inst, name="Science", code="SCI")
        self.client.force_login(self.admin)
        self.client.post(reverse("staff_detail", args=[self.teacher.pk]), {
            "job_title": "Teacher", "departments": [dept.pk],
        })
        profile = StaffProfile.all_objects.get(user=self.teacher)
        with use_institution(self.inst):
            self.assertEqual(
                list(profile.departments.values_list("pk", flat=True)), [dept.pk]
            )

    def test_the_staff_list_shows_the_profile(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("staff_detail", args=[self.teacher.pk]), {
            "job_title": "Teacher",
        })
        resp = self.client.get(reverse("staff_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "teach")


class WorkflowScreenTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.inst = Institution.objects.create(
            name="Alpha School", slug="alpha", type="secondary")
        ensure_roles(cls.inst)
        cls.admin = User.objects.create_user(
            "boss", password="pw123456", role="admin", is_superuser=True,
            institution=cls.inst)
        cls.bursar = User.objects.create_user(
            "bur", password="pw123456", role="bursar", institution=cls.inst)
        with use_institution(cls.inst):
            cls.bursar_role = Role.objects.get(institution=cls.inst, code="bursar")
            cls.template = WorkflowTemplate.objects.create(
                institution=cls.inst, name="Refund approval", code="refund")
            cls.template.steps.create(
                order=1, name="Bursar", approver_role=cls.bursar_role)
        cls.bursar.primary_role = cls.bursar_role
        cls.bursar.save()

    def test_a_workflow_can_be_created_with_a_step(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("workflow_create"), {
            "name": "Leave approval", "code": "leave", "applies_to": "leave",
        })
        self.assertEqual(resp.status_code, 302)
        template = WorkflowTemplate.all_objects.get(
            institution=self.inst, code="leave")
        resp = self.client.post(reverse("workflow_detail", args=[template.pk]), {
            "action": "add_step", "role": self.bursar_role.pk, "name": "Bursar",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(template.steps.count(), 1)

    def test_a_step_can_be_removed(self):
        self.client.force_login(self.admin)
        step = self.template.steps.first()
        resp = self.client.post(reverse("workflow_detail", args=[self.template.pk]), {
            "action": "delete_step", "step": step.pk,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.template.steps.count(), 0)

    def test_a_user_without_the_permission_is_refused(self):
        self.client.force_login(self.bursar)
        resp = self.client.get(reverse("workflow_list"))
        self.assertEqual(resp.status_code, 403)


class ApprovalFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.inst = Institution.objects.create(
            name="Alpha School", slug="alpha", type="secondary")
        ensure_roles(cls.inst)
        cls.bursar = User.objects.create_user(
            "bur", password="pw123456", role="bursar", institution=cls.inst)
        cls.principal = User.objects.create_user(
            "prin", password="pw123456", role="admin", institution=cls.inst)
        with use_institution(cls.inst):
            cls.bursar_role = Role.objects.get(institution=cls.inst, code="bursar")
            cls.principal_role = Role.objects.get(
                institution=cls.inst, code="principal")
            cls.template = WorkflowTemplate.objects.create(
                institution=cls.inst, name="Refund approval", code="refund")
            cls.template.steps.create(
                order=1, name="Bursar", approver_role=cls.bursar_role)
            cls.template.steps.create(
                order=2, name="Principal", approver_role=cls.principal_role)
        cls.bursar.primary_role = cls.bursar_role
        cls.bursar.save()
        cls.principal.primary_role = cls.principal_role
        cls.principal.save()

    def _request(self):
        with use_institution(self.inst):
            return submit(self.template, self.inst, self.bursar, reason="Refund 12")

    def test_submitting_notifies_the_first_approver(self):
        from core.models import Notification

        approval = self._request()
        self.assertEqual(approval.current_step, 1)
        self.assertEqual(approval.status, ApprovalRequest.Status.PENDING)
        self.assertTrue(
            Notification.all_objects.filter(user=self.bursar, category="workflow").exists()
        )

    def test_approving_moves_to_the_next_step_and_notifies_it(self):
        from core.models import Notification

        approval = self._request()
        with use_institution(self.inst):
            approve(approval, self.bursar, "checked")
        self.assertEqual(approval.current_step, 2)
        self.assertEqual(approval.status, ApprovalRequest.Status.PENDING)
        self.assertTrue(
            Notification.all_objects.filter(user=self.principal, category="workflow").exists()
        )

    def test_the_last_approval_finishes_the_request(self):
        approval = self._request()
        with use_institution(self.inst):
            approve(approval, self.bursar, "checked")
            approve(approval, self.principal, "fine")
        self.assertEqual(approval.status, ApprovalRequest.Status.APPROVED)
        self.assertIsNotNone(approval.decided_at)
        self.assertEqual(
            ApprovalAction.objects.filter(request=approval).count(), 3
        )

    def test_rejecting_finishes_the_request_and_tells_the_requester(self):
        from core.models import Notification

        approval = self._request()
        with use_institution(self.inst):
            reject(approval, self.bursar, "not allowed")
        self.assertEqual(approval.status, ApprovalRequest.Status.REJECTED)
        self.assertTrue(
            Notification.all_objects.filter(user=self.bursar, category="workflow").exists()
        )

    def test_somebody_who_is_not_the_approver_cannot_decide(self):
        from core.workflow import WorkflowError

        approval = self._request()
        outsider = User.objects.create_user(
            "out", password="pw123456", role="lecturer", institution=self.inst)
        with use_institution(self.inst):
            self.assertFalse(can_act(approval, outsider))
            with self.assertRaises(WorkflowError):
                approve(approval, outsider, "trying")

    def test_the_approvals_page_shows_the_request(self):
        self._request()
        self.client.force_login(self.bursar)
        resp = self.client.get(reverse("approvals"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Refund approval")

    def test_deciding_from_the_page_records_the_action(self):
        approval = self._request()
        self.client.force_login(self.bursar)
        resp = self.client.post(reverse("approval_detail", args=[approval.pk]), {
            "decision": "approve", "comment": "checked",
        })
        self.assertEqual(resp.status_code, 302)
        approval.refresh_from_db()
        self.assertEqual(approval.current_step, 2)
        self.assertTrue(
            ApprovalAction.objects.filter(
                request=approval, decision=ApprovalAction.Decision.APPROVED
            ).exists()
        )

    def test_a_request_from_another_institution_is_not_found(self):
        other = Institution.objects.create(name="Beta", slug="beta", type="secondary")
        with use_institution(other):
            foreign = ApprovalRequest.objects.create(
                institution=other, template=self.template, subject_type="X",
                subject_id="1")
        self.client.force_login(self.bursar)
        resp = self.client.get(reverse("approval_detail", args=[foreign.pk]))
        self.assertEqual(resp.status_code, 404)
