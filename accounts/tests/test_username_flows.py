"""Shared username rules, live check and identifier-based sign-in."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.forms import IDENTIFIER_AMBIGUOUS_MESSAGE, IDENTIFIER_INVALID_MESSAGE
from accounts.usernames import (
    USERNAME_HELP_TEXT,
    generate_username_for_email,
    reserve_username,
    validate_username,
)
from core.email_types import PASSWORD_RESET_REQUEST
from core.models import Institution, OutboundMessage
from core.notifications import absolute_url
from platform_admin.models import PlatformStaff
from platform_admin.services import ensure_platform_roles

User = get_user_model()
STRONG = "Str0ngPassw0rd"
TOTP_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


class UsernameRuleTests(TestCase):
    def test_valid_usernames_are_normalised_to_lowercase(self):
        cases = {
            "student.one": "student.one",
            "Mixed_Case-2026": "mixed_case-2026",
            "1234": "1234",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(validate_username(value), expected)

    def test_invalid_usernames_have_clear_errors(self):
        cases = (
            ("abc", "4 to 30 characters"),
            ("a" * 31, "4 to 30 characters"),
            ("two words", "cannot contain spaces"),
            ("bad@name", "Use only letters, numbers"),
            ("-leading", "Start with a letter"),
            ("ADMIN", "reserved"),
        )
        for value, message in cases:
            with self.subTest(value=value), self.assertRaisesMessage(ValidationError, message):
                validate_username(value)

    def test_generation_uses_the_email_local_part_and_adds_a_number_when_taken(self):
        first = generate_username_for_email("Jo.Bloggs@Example.test", "Jo Bloggs")
        second = generate_username_for_email("jo.bloggs@example.test", "Other Person")
        self.assertEqual(first, "jo.bloggs")
        self.assertEqual(second, "jo.bloggs1")

    def test_generated_usernames_meet_the_minimum_length(self):
        short = generate_username_for_email("a@example.test", "A")
        self.assertTrue(short.startswith("a"))
        self.assertGreaterEqual(len(short), 4)

    def test_reserved_email_local_part_gets_a_safe_suffix(self):
        username = generate_username_for_email("admin@example.test", "Admin Person")
        self.assertEqual(username, "admin1")

    def test_a_retired_username_cannot_be_reused(self):
        user = User.objects.create_user("retired", password=STRONG)
        reserve_username(user.username)
        user.delete()
        with self.assertRaisesMessage(ValidationError, "already in use"):
            validate_username("retired")

    def test_a_person_can_keep_their_own_reserved_username_on_edit(self):
        user = User.objects.create_user("existing", password=STRONG)
        reserve_username(user.username)
        self.assertEqual(
            validate_username("EXISTING", exclude_user=user), "existing"
        )


class UsernameAvailabilityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.viewer = User.objects.create_user("viewer", password=STRONG)
        self.taken = User.objects.create_user("takenone", password=STRONG)
        self.url = reverse("username_availability")

    def test_the_endpoint_requires_a_signed_in_user_and_get(self):
        self.assertEqual(self.client.get(self.url, {"username": "freename"}).status_code, 302)
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.post(self.url, {"username": "freename"}).status_code, 405)

    def test_available_taken_reserved_and_invalid_names(self):
        self.client.force_login(self.viewer)
        available = self.client.get(self.url, {"username": "Free.Name_1"})
        self.assertEqual(available.json(), {
            "available": True,
            "username": "free.name_1",
            "message": "This username is available",
        })

        for value in ("takenone", "ADMIN", "ab", "two words"):
            with self.subTest(value=value):
                response = self.client.get(self.url, {"username": value})
                self.assertFalse(response.json()["available"])
                self.assertTrue(response.json()["message"])

    def test_an_edit_can_exclude_its_own_account(self):
        self.client.force_login(self.viewer)
        response = self.client.get(self.url, {
            "username": self.taken.username, "exclude": self.taken.pk,
        })
        self.assertTrue(response.json()["available"])

    def test_optional_username_help_text_is_exact(self):
        self.assertEqual(
            USERNAME_HELP_TEXT,
            "Leave blank and we will create one from the email address.",
        )


class SignInBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alpha = Institution.objects.create(
            name="Alpha College", slug="alpha", type="college",
            public_registration=True,
        )
        cls.beta = Institution.objects.create(
            name="Beta College", slug="beta", type="college",
            public_registration=True,
        )
        cls.alpha_user = User.objects.create_user(
            "alpha.student", password=STRONG, role=User.Role.STUDENT,
            institution=cls.alpha, email="shared@example.test",
        )
        cls.beta_user = User.objects.create_user(
            "beta.student", password=STRONG, role=User.Role.STUDENT,
            institution=cls.beta, email="shared@example.test",
        )
        cls.solo = User.objects.create_user(
            "solo.user", password=STRONG, role=User.Role.ADMIN,
            institution=cls.alpha, email="solo@example.test",
        )

    def setUp(self):
        cache.clear()


class IdentifierSignInTests(SignInBase):
    def _post(self, url, identifier, password=STRONG):
        return self.client.post(url, {"username": identifier, "password": password})

    def test_both_sign_in_pages_label_the_identifier_field(self):
        for url in (reverse("login"), reverse("institution_login", args=["alpha"])):
            with self.subTest(url=url):
                page = self.client.get(url)
                self.assertContains(page, "Email or username")

    def test_a_unique_email_signs_in_case_insensitively(self):
        response = self._post(reverse("login"), "SOLO@EXAMPLE.TEST")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.solo.pk)

    def test_a_username_signs_in_case_insensitively(self):
        response = self._post(reverse("login"), "SOLO.USER")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.solo.pk)

    def test_unknown_identifier_and_wrong_password_share_one_error(self):
        for identifier, password in (("nobody@example.test", STRONG), ("solo.user", "wrong")):
            with self.subTest(identifier=identifier):
                self.client.logout()
                response = self._post(reverse("login"), identifier, password)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, IDENTIFIER_INVALID_MESSAGE)
                self.assertNotIn("_auth_user_id", self.client.session)

    def test_ambiguous_email_asks_for_a_username_or_institution_page(self):
        response = self._post(reverse("login"), "SHARED@EXAMPLE.TEST")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, IDENTIFIER_AMBIGUOUS_MESSAGE)
        self.assertNotIn("_auth_user_id", self.client.session)

        by_username = self._post(reverse("login"), "alpha.student")
        self.assertEqual(by_username.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.alpha_user.pk)
        self.client.logout()

        scoped = self._post(reverse("institution_login", args=["alpha"]), "shared@example.test")
        self.assertEqual(scoped.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.alpha_user.pk)

    def test_the_institution_page_hides_other_institution_accounts(self):
        response = self._post(reverse("institution_login", args=["beta"]), "solo@example.test")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, IDENTIFIER_INVALID_MESSAGE)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_lockout_applies_to_identifier_sign_in(self):
        url = reverse("login")
        for _ in range(8):
            self._post(url, "solo@example.test", "wrong")
        self.solo.refresh_from_db()
        self.assertIsNotNone(self.solo.locked_until)

        response = self._post(url, "SOLO.USER")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "temporarily locked")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_a_two_factor_platform_account_can_sign_in_by_email(self):
        roles = ensure_platform_roles()
        staff_user = User.objects.create_user(
            "platform.email", password=STRONG, role=User.Role.STAFF,
            institution=self.alpha, email="platform.email@example.test",
        )
        PlatformStaff.objects.create(
            user=staff_user, role=roles["support"], is_active=True,
            two_factor_enabled=True, two_factor_secret=TOTP_SECRET,
            last_reauth_at=timezone.now(),
        )
        response = self._post(reverse("login"), "PLATFORM.EMAIL@EXAMPLE.TEST")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), staff_user.pk)
        self.assertEqual(self.client.get("/platform/").status_code, 200)
        staff_user.refresh_from_db()
        self.assertTrue(staff_user.platform_staff.two_factor_enabled)


class PasswordResetIdentifierTests(SignInBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        mail.outbox = []

    def _post_reset(self, identifier):
        return self.client.post(reverse("password_reset"), {"email": identifier})

    def _instruction(self, user):
        institution = user.institution
        if institution and getattr(institution, "slug", ""):
            login_link = absolute_url(reverse("institution_login", args=[institution.slug]))
        else:
            login_link = absolute_url(reverse("login"))
        return f"Sign in at {login_link} with your username {user.username} or your email address."

    def _html_instruction(self, user):
        institution = user.institution
        if institution and getattr(institution, "slug", ""):
            login_link = absolute_url(reverse("institution_login", args=[institution.slug]))
        else:
            login_link = absolute_url(reverse("login"))
        return (
            f'<a href="{login_link}" style="color:#5d4037;">{login_link}</a> '
            f'with your username <strong style="color:#2b1a12;">{user.username}</strong> '
            "or your email address."
        )

    def _assert_reset_sent(self, identifier, user):
        with override_settings(SITE_URL="https://school.example"):
            response = self._post_reset(identifier)
            instruction = self._instruction(user)
            html_instruction = self._html_instruction(user)
        self.assertRedirects(
            response, reverse("password_reset_done"), fetch_redirect_response=False
        )
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertIn(instruction, sent.body)
        self.assertTrue(sent.alternatives)
        self.assertIn(html_instruction, sent.alternatives[0][0])
        self.assertNotIn(STRONG, sent.body)
        logged = OutboundMessage.all_objects.get(
            recipient=user.email, email_type=PASSWORD_RESET_REQUEST
        )
        self.assertIn(html_instruction, logged.html_body)
        self.assertIn(instruction, logged.body)
        self.assertNotIn(STRONG, logged.body)

    def test_password_reset_works_with_email_case_insensitively(self):
        self._assert_reset_sent(" SOLO@EXAMPLE.TEST ", self.solo)

    def test_password_reset_works_with_username_case_insensitively(self):
        self._assert_reset_sent("SOLO.USER", self.solo)

    def test_password_reset_does_not_reveal_unknown_accounts(self):
        with override_settings(SITE_URL="https://school.example"):
            known = self._post_reset("solo@example.test")
            count_after_known = OutboundMessage.all_objects.filter(
                email_type=PASSWORD_RESET_REQUEST
            ).count()
            mail.outbox = []
            unknown = self._post_reset("nobody@example.test")
        self.assertRedirects(
            known, reverse("password_reset_done"), fetch_redirect_response=False
        )
        self.assertRedirects(
            unknown, reverse("password_reset_done"), fetch_redirect_response=False
        )
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(
            OutboundMessage.all_objects.filter(email_type=PASSWORD_RESET_REQUEST).count(),
            count_after_known,
        )

    def test_unknown_identifier_gives_same_done_page_as_known(self):
        with override_settings(SITE_URL="https://school.example"):
            known = self.client.post(reverse("password_reset"), {"email": "missing.user"}, follow=True)
            unknown = self.client.post(
                reverse("password_reset"), {"email": "nobody@example.test"}, follow=True
            )
        self.assertEqual(known.status_code, 200)
        self.assertEqual(unknown.status_code, 200)
        self.assertContains(unknown, "Check your email")
        self.assertContains(known, "Check your email")
        self.assertNotContains(unknown, "No account")
        self.assertNotContains(known, "No account")


class ProvisioningInstructionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.inst = Institution.objects.create(
            name="Provision College", slug="provision", type="college"
        )
        from core.roles import ensure_roles

        cls.roles = ensure_roles(cls.inst)
        cls.admin = User.objects.create_user(
            "provadmin", password=STRONG, role=User.Role.ADMIN,
            institution=cls.inst, email="provadmin@example.test",
        )
        cls.admin.primary_role = cls.roles["institution_admin"]
        cls.admin.save(update_fields=["primary_role"])

    def setUp(self):
        cache.clear()
        mail.outbox = []

    @staticmethod
    def instruction(user):
        login_link = absolute_url(reverse("institution_login", args=[user.institution.slug]))
        return f"Sign in at {login_link} with your username {user.username} or your email address."

    def test_admin_temp_password_confirmation_shows_username_and_instruction_once(self):
        self.client.force_login(self.admin)
        role = self.roles["lecturer"]
        with override_settings(SITE_URL="https://school.example"):
            response = self.client.post(
                reverse("user_add_staff"),
                {
                    "full_name": "Generated Staff",
                    "username": "",
                    "email": "generated.staff@example.test",
                    "phone": "",
                    "role": role.pk,
                    "departments": [],
                    "job_title": "Lecturer",
                    "staff_no": "EMP-NEW-1",
                    "start_date": "",
                    "access_mode": "temp_password",
                },
            )
        self.assertRedirects(response, reverse("user_created"))
        account = User.objects.get(email="generated.staff@example.test")
        self.assertEqual(account.username, "generated.staff")
        page = self.client.get(reverse("user_created"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, account.username)
        self.assertContains(page, f"with your username <strong>{account.username}</strong> or your email address.")
        self.assertContains(page, page.context["credentials"]["password"].replace("&", "&amp;"))
        self.assertNotContains(page, "An invitation email was sent")
        again = self.client.get(reverse("user_created"))
        self.assertRedirects(again, reverse("user_list"))
        # No password in any outbound email for this provisioning.
        for msg in OutboundMessage.all_objects.filter(recipient=account.email):
            self.assertNotIn(page.context.get("credentials", {}).get("password", "") or "impossible", msg.body)

    def test_admin_invitation_confirmation_and_email_show_username(self):
        self.client.force_login(self.admin)
        role = self.roles["lecturer"]
        with override_settings(SITE_URL="https://school.example"):
            response = self.client.post(
                reverse("user_add_staff"),
                {
                    "full_name": "Invited Staff",
                    "username": "",
                    "email": "invited.staff@example.test",
                    "phone": "",
                    "role": role.pk,
                    "departments": [],
                    "job_title": "Lecturer",
                    "staff_no": "EMP-INV-1",
                    "start_date": "",
                    "access_mode": "invite",
                },
            )
        self.assertRedirects(response, reverse("user_created"))
        account = User.objects.get(email="invited.staff@example.test")
        self.assertEqual(account.username, "invited.staff")
        page = self.client.get(reverse("user_created"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, f"with your username <strong>{account.username}</strong> or your email address.")
        self.assertContains(page, "An invitation email was sent")
        invitation_messages = OutboundMessage.all_objects.filter(
            recipient=account.email
        )
        self.assertTrue(invitation_messages.exists())
        with override_settings(SITE_URL="https://school.example"):
            expected_instruction = self.instruction(account)
        self.assertTrue(
            any(expected_instruction in (m.body or "") for m in invitation_messages)
        )
        self.assertTrue(
            any(f'with your username <strong style="color:#2b1a12;">{account.username}</strong> or your email address.' in (m.html_body or "") for m in invitation_messages)
        )
        self.assertFalse(any(STRONG in (m.body or "") for m in invitation_messages))

    def test_generated_usernames_are_unique_and_deleted_names_are_not_reused(self):
        from accounts.usernames import generate_username_for_email, reserve_username

        first = generate_username_for_email("repeat.person@example.test", "")
        self.assertEqual(first, "repeat.person")
        User.objects.create_user(first, password=STRONG, institution=self.inst)
        second = generate_username_for_email("repeat.person@example.test", "")
        self.assertEqual(second, "repeat.person1")

        doomed = User.objects.create_user("retired.flow", password=STRONG, institution=self.inst)
        reserve_username(doomed.username)
        doomed.delete()
        third = generate_username_for_email("retired.flow@example.test", "")
        self.assertNotEqual(third, "retired.flow")
        self.assertTrue(third.startswith("retired.flow"))
