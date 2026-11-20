"""Role-aware in-app help tests."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.help import (
    ADMIN,
    FINANCE,
    GENERAL,
    GUIDES,
    PARENT,
    PLATFORM,
    REGISTRAR,
    STUDENT,
    TEACHING,
    guide_for,
)

User = get_user_model()


class GuideSelectionTests(TestCase):
    def test_every_role_maps_to_a_guide(self):
        for role, _label in User.Role.choices:
            user = User(role=role)
            self.assertIn(guide_for(user), GUIDES, f"no guide for {role}")

    def test_platform_flag_wins_over_the_role(self):
        user = User(role="student", is_platform_admin=True)
        self.assertEqual(guide_for(user), PLATFORM)

    def test_known_roles_map_to_their_specialist_guides(self):
        cases = {
            "admin": ADMIN,
            "registrar": REGISTRAR,
            "bursar": FINANCE,
            "accountant": FINANCE,
            "lecturer": TEACHING,
            "teacher": TEACHING,
            "student": STUDENT,
            "parent": PARENT,
        }
        for role, expected in cases.items():
            with self.subTest(role=role):
                self.assertEqual(guide_for(User(role=role)), expected)

    def test_unknown_roles_use_the_general_guide(self):
        self.assertEqual(guide_for(User(role="visitor")), GENERAL)


class HelpPageTests(TestCase):
    def test_help_requires_a_login(self):
        response = self.client.get(reverse("help_center"))
        self.assertEqual(response.status_code, 302)

    def test_a_student_sees_the_student_guide(self):
        user = User.objects.create_user("help-student", password="pw123456",
                                        role="student")
        self.client.force_login(user)
        response = self.client.get(reverse("help_center"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Help for Student")
        self.assertContains(response, "Common tasks")

    def test_help_links_are_resolved_to_real_urls(self):
        user = User.objects.create_user("help-admin", password="pw123456",
                                        role="admin", is_staff=True,
                                        is_superuser=True)
        self.client.force_login(user)
        response = self.client.get(reverse("help_center"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Help for Institution administrator")
        self.assertContains(response, "/imports/new/")
