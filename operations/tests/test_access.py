from django.test import TestCase
from django.urls import reverse

from accounts.factories import UserFactory
from core.models import Institution, Role
from core.modules import set_module
from core.roles import ensure_roles
from operations.registry import MODULES


class OperationsAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.institution = Institution.objects.get(slug="default")
        ensure_roles(cls.institution)
        for code, info in MODULES.items():
            set_module(cls.institution, info.get("flag", code), True)
        cls.admin = UserFactory(username="ops_admin", role="admin", institution=cls.institution)
        cls.student = UserFactory(
            username="ops_student", role="student", institution=cls.institution
        )

    def _officer(self, role_code, username):
        user = UserFactory(username=username, role="student", institution=self.institution)
        user.primary_role = Role.all_objects.get(institution=self.institution, code=role_code)
        user.save()
        return user

    def test_admin_can_open_every_module_and_register(self):
        self.client.force_login(self.admin)
        for code, module in MODULES.items():
            response = self.client.get(reverse("operations:module", args=[code]))
            self.assertEqual(response.status_code, 200, code)
            self.assertContains(response, module["title"])
            for kind in module["kinds"]:
                response = self.client.get(reverse("operations:list", args=[code, kind["slug"]]))
                self.assertEqual(response.status_code, 200, f"{code}/{kind['slug']}")

    def test_the_hub_lists_the_administration_modules(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("operations:hub"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Human resources")
        self.assertContains(response, "Payroll")
        self.assertContains(response, "Governance")

    def test_an_hr_officer_sees_hr_but_not_payroll(self):
        officer = self._officer("hr_officer", "ops_hr")
        self.client.force_login(officer)
        hub = self.client.get(reverse("operations:hub"))
        self.assertContains(hub, "Human resources")
        self.assertNotContains(hub, "Payroll")
        self.assertEqual(
            self.client.get(reverse("operations:module", args=["hr"])).status_code, 200
        )
        self.assertEqual(
            self.client.get(reverse("operations:module", args=["payroll"])).status_code, 403
        )

    def test_a_user_without_a_role_is_refused_and_sees_an_empty_hub(self):
        self.client.force_login(self.student)
        hub = self.client.get(reverse("operations:hub"))
        self.assertEqual(hub.status_code, 200)
        self.assertContains(hub, "No administration modules")
        response = self.client.get(reverse("operations:module", args=["hr"]))
        self.assertEqual(response.status_code, 403)

    def test_a_switched_off_module_is_not_found(self):
        set_module(self.institution, "estates", False)
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.get(reverse("operations:module", args=["estates"])).status_code, 404
        )
        self.assertEqual(
            self.client.get(
                reverse("operations:list", args=["estates", "work-orders"])
            ).status_code,
            404,
        )
        hub = self.client.get(reverse("operations:hub"))
        self.assertNotContains(hub, "Estates and maintenance")

    def test_an_unknown_module_or_register_is_not_found(self):
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.get(reverse("operations:module", args=["nothing"])).status_code, 404
        )
        self.assertEqual(
            self.client.get(reverse("operations:list", args=["hr", "nothing"])).status_code, 404
        )

    def test_the_administration_link_is_shown_to_staff_with_a_module(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, reverse("operations:hub"))
