"""Phase 1: feature flags hide a module from the menu and from the pages."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import Institution, InstitutionModule
from core.modules import ALL_MODULES, apply_defaults, is_enabled, set_module

User = get_user_model()


class ModuleSwitchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.inst = Institution.objects.create(
            name="Alpha School", slug="alpha", type="secondary")
        cls.admin = User.objects.create_user(
            "boss", password="pw123456", role="admin", is_staff=True,
            is_superuser=True, institution=cls.inst)

    def test_the_defaults_depend_on_the_institution_type(self):
        from core.tenant_defaults import DEFAULT_MODULES

        apply_defaults(self.inst)
        codes = set(self.inst.modules.filter(enabled=True).values_list("code", flat=True))
        self.assertEqual(codes, set(DEFAULT_MODULES["secondary"]))

    def test_a_module_can_be_switched_off_and_on(self):
        set_module(self.inst, "library", False)
        self.assertFalse(is_enabled("library", self.inst))
        set_module(self.inst, "library", True)
        self.assertTrue(is_enabled("library", self.inst))

    def test_switches_belong_to_one_institution(self):
        other = Institution.objects.create(name="Beta School", slug="beta", type="secondary")
        set_module(self.inst, "library", False)
        set_module(other, "library", True)
        self.assertFalse(is_enabled("library", self.inst))
        self.assertTrue(is_enabled("library", other))

    def test_every_module_in_the_plan_has_a_switch(self):
        for code in ("core_academics", "finance", "admissions", "parent_portal",
                     "exams", "library", "health", "counselling", "sports",
                     "boarding", "transport", "catering", "careers_attachment",
                     "research", "labs", "ict_helpdesk", "hr", "payroll",
                     "procurement_stores", "estates", "security", "compliance",
                     "marketing", "governance", "api", "sso", "lms",
                     "student_affairs"):
            self.assertIn(code, ALL_MODULES)


class ModuleGateTests(TestCase):
    """A module that is switched off disappears from the menu and the pages."""

    @classmethod
    def setUpTestData(cls):
        cls.inst = Institution.objects.create(
            name="Alpha School", slug="alpha", type="secondary")
        cls.admin = User.objects.create_user(
            "boss", password="pw123456", role="admin", is_staff=True,
            is_superuser=True, institution=cls.inst)
        apply_defaults(cls.inst)

    def test_a_finance_page_is_hidden_when_finance_is_off(self):
        set_module(self.inst, "finance", False)
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("report_debtors"))
        self.assertEqual(resp.status_code, 404)

    def test_a_finance_page_works_when_finance_is_on(self):
        set_module(self.inst, "finance", True)
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("report_debtors"))
        self.assertEqual(resp.status_code, 200)

    def test_the_menu_hides_a_switched_off_module(self):
        set_module(self.inst, "finance", False)
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("dashboard"))
        content = resp.content.decode()
        self.assertNotIn(reverse("report_debtors"), content)
        self.assertIn(reverse("dashboard"), content)

    def test_the_settings_page_shows_every_switch(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("institution_settings"))
        self.assertEqual(resp.status_code, 200)
        for code in ("finance", "exams", "library"):
            self.assertContains(resp, code)

    def test_switching_a_module_off_from_the_page(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("institution_settings"), {
            "action": "module", "code": "library", "enabled": "off",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            InstitutionModule.objects.filter(
                institution=self.inst, code="library", enabled=True).exists()
        )

    def test_an_unknown_module_cannot_be_switched(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("institution_settings"), {
            "action": "module", "code": "not_a_module", "enabled": "off",
        })
        self.assertFalse(
            InstitutionModule.objects.filter(code="not_a_module").exists()
        )
