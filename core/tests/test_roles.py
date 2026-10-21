"""Phase 1: roles as data, the permission matrix, and the screens that use them."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.access import permission_codes_for, user_has_code
from core.models import Permission, Role
from core.roles import DEFAULT_ROLES, LEGACY_ROLE_MAP, ensure_permissions, ensure_roles
from core.tenancy import use_institution

User = get_user_model()


class RoleSeedingTests(TestCase):
    def test_every_default_role_is_created(self):
        inst = self._institution()
        ensure_roles(inst)
        with use_institution(inst):
            codes = set(Role.objects.filter(institution=inst).values_list("code", flat=True))
        for spec in DEFAULT_ROLES:
            self.assertIn(spec["code"], codes)

    def test_the_institution_admin_holds_every_permission(self):
        inst = self._institution()
        ensure_roles(inst)
        with use_institution(inst):
            admin = Role.objects.get(institution=inst, code="institution_admin")
            held = set(admin.role_permissions.values_list("permission__code", flat=True))
        self.assertEqual(held, set(Permission.objects.values_list("code", flat=True)))

    def test_the_bursar_does_not_hold_admin_permissions(self):
        inst = self._institution()
        ensure_roles(inst)
        with use_institution(inst):
            bursar = Role.objects.get(institution=inst, code="bursar")
            held = set(bursar.role_permissions.values_list("permission__code", flat=True))
        self.assertIn("finance.receive_payment", held)
        self.assertNotIn("admin.manage_roles", held)
        self.assertNotIn("students.create", held)

    def test_roles_are_not_shared_between_institutions(self):
        first = self._institution("first", "First School")
        second = self._institution("second", "Second School")
        ensure_roles(first)
        with use_institution(second):
            self.assertEqual(Role.objects.count(), 0)
        with use_institution(first):
            self.assertTrue(Role.objects.count() > 0)

    def test_ensure_permissions_can_be_run_twice(self):
        self.assertEqual(ensure_permissions(), 0)
        self.assertEqual(ensure_permissions(), 0)

    def _institution(self, slug="alpha", name="Alpha School"):
        from core.models import Institution

        return Institution.objects.create(name=name, slug=slug, type="secondary")


class LegacyRoleMappingTests(TestCase):
    def test_the_old_role_names_map_onto_the_new_codes(self):
        self.assertEqual(LEGACY_ROLE_MAP["admin"], "institution_admin")
        self.assertEqual(LEGACY_ROLE_MAP["registrar"], "registrar")
        self.assertEqual(LEGACY_ROLE_MAP["bursar"], "bursar")
        self.assertEqual(LEGACY_ROLE_MAP["lecturer"], "lecturer")
        self.assertEqual(LEGACY_ROLE_MAP["student"], "student")
        self.assertEqual(LEGACY_ROLE_MAP["parent"], "parent")

    def test_an_old_admin_user_gets_the_new_permissions(self):
        inst = self._institution()
        ensure_roles(inst)
        user = User.objects.create_user("old_admin", password="pw123456", role="admin")
        self.assertTrue(user_has_code(user, "admin.manage_roles"))
        self.assertTrue(user_has_code(user, "finance.receive_payment"))

    def _institution(self, slug="gamma", name="Gamma School"):
        from core.models import Institution

        return Institution.objects.create(name=name, slug=slug, type="secondary")


class PermissionMatrixViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from core.models import Institution

        cls.inst = Institution.objects.create(
            name="Alpha School", slug="alpha", type="secondary")
        ensure_roles(cls.inst)
        cls.admin = User.objects.create_user(
            "boss", password="pw123456", role="admin", is_staff=True, is_superuser=True,
            institution=cls.inst)
        cls.bursar = User.objects.create_user(
            "bur", password="pw123456", role="bursar", institution=cls.inst)

    def test_the_roles_page_lists_the_roles(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("role_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Registrar")
        self.assertContains(resp, "admin.manage_roles")

    def test_a_user_without_the_permission_is_refused(self):
        self.client.force_login(self.bursar)
        resp = self.client.get(reverse("role_list"))
        self.assertEqual(resp.status_code, 403)

    def test_saving_the_matrix_adds_and_removes_permissions(self):
        self.client.force_login(self.admin)
        registrar = Role.all_objects.get(institution=self.inst, code="registrar")
        self.assertFalse(
            registrar.role_permissions.filter(permission__code="admin.manage_roles").exists()
        )
        resp = self.client.post(reverse("role_matrix"), {
            f"p_{registrar.pk}_admin.manage_roles": "on",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            registrar.role_permissions.filter(permission__code="admin.manage_roles").exists()
        )
        # Post again without the box ticked: the permission goes away again.
        self.client.post(reverse("role_matrix"), {})
        self.assertFalse(
            registrar.role_permissions.filter(permission__code="admin.manage_roles").exists()
        )

    def test_a_role_can_be_created_and_removed(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("role_create"), {
            "name": "Head of boarding", "code": "head_of_boarding",
            "description": "Runs the hostels.",
        })
        self.assertEqual(resp.status_code, 302)
        role = Role.all_objects.get(institution=self.inst, code="head_of_boarding")
        resp = self.client.post(reverse("role_delete", args=[role.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Role.all_objects.filter(code="head_of_boarding").exists())

    def test_a_built_in_role_cannot_be_removed(self):
        self.client.force_login(self.admin)
        registrar = Role.all_objects.get(institution=self.inst, code="registrar")
        resp = self.client.post(reverse("role_delete", args=[registrar.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Role.all_objects.filter(code="registrar").exists())

    def test_a_role_from_another_institution_is_not_found(self):
        from core.models import Institution

        other = Institution.objects.create(name="Beta School", slug="beta", type="secondary")
        with use_institution(other):
            ensure_roles(other)
        foreign = Role.all_objects.filter(institution=other).first()
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("role_detail", args=[foreign.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_the_matrix_change_is_written_to_the_audit_log(self):
        from core.models import AuditEntry

        self.client.force_login(self.admin)
        registrar = Role.all_objects.get(institution=self.inst, code="registrar")
        self.client.post(reverse("role_matrix"), {
            f"p_{registrar.pk}_admin.manage_roles": "on",
        })
        self.assertTrue(
            AuditEntry.all_objects.filter(action="roles.matrix_saved").exists()
        )


class PermissionCodeTests(TestCase):
    def test_a_superuser_holds_every_code(self):
        user = User.objects.create_user(
            "root", password="pw123456", role="admin", is_superuser=True)
        self.assertTrue(user_has_code(user, "admin.manage_settings"))
        self.assertTrue(user_has_code(user, "finance.receive_payment"))

    def test_an_anonymous_user_holds_nothing(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertEqual(permission_codes_for(AnonymousUser()), set())
