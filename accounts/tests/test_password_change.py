"""Regression tests for every password form after the Django 5.1 clean() move.

Django 5.1 removed ``clean_new_password2()``. Each form here must report
validation errors (200, or an invalid form) and never return a server error.
"""

from datetime import timedelta

from django.contrib.auth import HASH_SESSION_KEY, SESSION_KEY, get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts.admin_forms import (
    InvitationPasswordForm,
    StrongAdminPasswordChangeForm,
    StrongPasswordChangeForm,
    StrongSetPasswordForm,
    StrongUserCreationForm,
)
from accounts.forms import StaffRegistrationForm, StudentRegistrationForm
from accounts.models import UserInvitation
from core.models import Institution

User = get_user_model()
TEMPORARY = "TempPass1234"
STRONG = "Str0ngPassw0rd"
SHORT = "Sh0rt1"
COMMON = "password123"
OTHER_STRONG = "An0therStrong1"


class ForcePasswordChangeFlowTests(TestCase):
    def setUp(self):
        institution = Institution.objects.create(
            name="Password College", slug="password-college"
        )
        self.user = User.objects.create_user(
            "tempuser",
            password=TEMPORARY,
            role=User.Role.STUDENT,
            institution=institution,
            email="tempuser@password.test",
            must_change_password=True,
            password_set_from_env=True,
        )
        self.assertTrue(self.client.login(username="tempuser", password=TEMPORARY))

    def change(self, **overrides):
        payload = {
            "old_password": TEMPORARY,
            "new_password1": STRONG,
            "new_password2": STRONG,
        }
        payload.update(overrides)
        return self.client.post(reverse("password_change"), payload)

    def assertBadRequest(self, response, text):
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, text)

    def test_the_forced_password_change_page_opens(self):
        response = self.client.get(reverse("password_change"))
        self.assertEqual(response.status_code, 200)

    def test_a_valid_password_clears_the_flags_keeps_the_session_and_opens_the_portal(self):
        response = self.change()
        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)

        self.user.refresh_from_db()
        self.assertFalse(self.user.must_change_password)
        self.assertFalse(self.user.password_set_from_env)
        self.assertTrue(self.user.check_password(STRONG))
        self.assertEqual(str(self.client.session[SESSION_KEY]), str(self.user.pk))
        self.assertEqual(self.client.session[HASH_SESSION_KEY], self.user.get_session_auth_hash())

        portal = self.client.get(reverse("dashboard"))
        self.assertRedirects(portal, reverse("student_portal"), fetch_redirect_response=False)

        self.client.logout()
        self.assertTrue(self.client.login(username="tempuser", password=STRONG))

    def test_a_platform_admin_reaches_the_platform_portal(self):
        admin = User.objects.create_user(
            "platformtemp",
            password=TEMPORARY,
            role=User.Role.ADMIN,
            is_platform_admin=True,
            must_change_password=True,
        )
        self.client.force_login(admin)
        response = self.client.post(reverse("password_change"), {
            "old_password": TEMPORARY,
            "new_password1": STRONG,
            "new_password2": STRONG,
        })
        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)
        portal = self.client.get(reverse("dashboard"))
        self.assertRedirects(
            portal, reverse("platform_admin:dashboard"), fetch_redirect_response=False
        )

    def test_bad_passwords_show_errors_without_a_server_error(self):
        for label, overrides, text in (
            ("mismatch", {"new_password2": OTHER_STRONG}, "match"),
            ("too short", {"new_password1": SHORT, "new_password2": SHORT}, "10 characters"),
            ("too common", {"new_password1": COMMON, "new_password2": COMMON}, "common"),
        ):
            with self.subTest(case=label):
                response = self.change(**overrides)
                self.assertBadRequest(response, text)
                self.user.refresh_from_db()
                self.assertTrue(self.user.must_change_password)
                self.assertTrue(self.user.password_set_from_env)
                self.assertTrue(self.user.check_password(TEMPORARY))

    def test_a_wrong_old_password_does_not_change_the_account(self):
        response = self.change(old_password="NotTheOldPassword1")
        self.assertBadRequest(response, "old password")
        self.user.refresh_from_db()
        self.assertTrue(self.user.must_change_password)
        self.assertTrue(self.user.check_password(TEMPORARY))

    def test_the_next_page_set_by_the_middleware_is_honoured(self):
        target = reverse("student_portal")
        self.client.get(f"{reverse('password_change')}?next={target}")
        response = self.change(next=target)
        self.assertRedirects(response, target, fetch_redirect_response=False)

    def test_an_external_next_is_not_used_as_an_open_redirect(self):
        response = self.change(next="https://evil.example/phish")
        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)


class PasswordFormValidationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.institution = Institution.objects.create(
            name="Password College",
            slug="password-college",
            public_registration=True,
            staff_positions=["teacher"],
            contact_email="registrar@password.test",
            contact_phone="+263771000111",
        )
        cls.user = User.objects.create_user(
            "tempuser",
            password=TEMPORARY,
            role=User.Role.STUDENT,
            institution=cls.institution,
            email="tempuser@password.test",
        )

    def factories(self, password, confirm):
        """One factory per password form: (name, form, field, has_confirmation)."""
        return [
            (
                "password change",
                lambda: StrongPasswordChangeForm(self.user, {
                    "old_password": TEMPORARY,
                    "new_password1": password,
                    "new_password2": confirm,
                }),
                "new_password2",
                True,
            ),
            (
                "password reset",
                lambda: StrongSetPasswordForm(self.user, {
                    "new_password1": password,
                    "new_password2": confirm,
                }),
                "new_password2",
                True,
            ),
            (
                "set-password invitation",
                lambda: InvitationPasswordForm({
                    "password": password,
                    "password_confirm": confirm,
                }, user=self.user),
                "password",
                True,
            ),
            (
                "student registration",
                lambda: StudentRegistrationForm(data={
                    "institution": self.institution.pk,
                    "registration_number": "ST-001",
                    "surname": "Moyo",
                    "date_of_birth": "2010-05-04",
                    "contact": "tariro@example.test",
                    "username": "tariro",
                    "password": password,
                }),
                "password",
                False,
            ),
            (
                "staff registration",
                lambda: StaffRegistrationForm(data={
                    "institution": self.institution.pk,
                    "full_name": "Farai Ncube",
                    "email": "farai@example.test",
                    "phone": "0771234567",
                    "position": "teacher",
                    "username": "farai",
                    "password": password,
                }),
                "password",
                False,
            ),
            (
                "admin add user",
                lambda: StrongUserCreationForm(data={
                    "username": "newadmin",
                    "password1": password,
                    "password2": confirm,
                }),
                "password2",
                True,
            ),
            (
                "admin change password",
                lambda: StrongAdminPasswordChangeForm(self.user, {
                    "password1": password,
                    "password2": confirm,
                }),
                "password2",
                True,
            ),
        ]

    def test_every_password_form_handles_mismatch_short_common_and_valid(self):
        cases = (
            ("mismatched", STRONG, OTHER_STRONG, False),
            ("too short", SHORT, SHORT, False),
            ("too common", COMMON, COMMON, False),
            ("valid", STRONG, STRONG, True),
        )
        for label, password, confirm, expected in cases:
            for name, factory, field, has_confirmation in self.factories(password, confirm):
                if label == "mismatched" and not has_confirmation:
                    continue
                with self.subTest(form=name, case=label):
                    form = factory()
                    # is_valid() must return a result, not raise a 500-worthy error.
                    self.assertEqual(form.is_valid(), expected, form.errors)
                    if not expected:
                        self.assertTrue(form.errors, f"{name}.{field}: {form.errors}")


class PasswordResetConfirmFlowTests(TestCase):
    def url_for(self, user):
        uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        return reverse("password_reset_confirm", kwargs={"uidb64": uidb64, "token": token})

    def test_every_reset_password_case_returns_a_page_not_a_server_error(self):
        for label, password, confirm, expected_status in (
            ("mismatched", STRONG, OTHER_STRONG, 200),
            ("too short", SHORT, SHORT, 200),
            ("too common", COMMON, COMMON, 200),
            ("valid", STRONG, STRONG, 302),
        ):
            with self.subTest(case=label):
                institution = Institution.objects.create(
                    name=f"Reset {label}", slug=f"reset-{label.replace(' ', '-')}"
                )
                user = User.objects.create_user(
                    f"reset-{label.replace(' ', '-')}",
                    password=TEMPORARY,
                    role=User.Role.STUDENT,
                    institution=institution,
                )
                url = self.url_for(user)
                landing = self.client.get(url, follow=True)
                self.assertEqual(landing.status_code, 200)
                response = self.client.post(landing.request["PATH_INFO"], {
                    "new_password1": password,
                    "new_password2": confirm,
                })
                self.assertEqual(response.status_code, expected_status)
                user.refresh_from_db()
                self.assertEqual(user.check_password(STRONG), label == "valid")


class InvitationFlowTests(TestCase):
    def invitation_for(self, username):
        institution = Institution.objects.create(
            name=f"Invite {username}", slug=f"invite-{username}"
        )
        user = User.objects.create_user(
            username, password=TEMPORARY, role=User.Role.STAFF, institution=institution
        )
        user.set_unusable_password()
        user.save(update_fields=["password"])
        invitation = UserInvitation.objects.create(
            user=user, expires_at=timezone.now() + timedelta(hours=72)
        )
        return user, invitation

    def test_every_invitation_password_case_returns_a_page_not_a_server_error(self):
        for label, password, confirm, expected_status in (
            ("mismatched", STRONG, OTHER_STRONG, 200),
            ("too short", SHORT, SHORT, 200),
            ("too common", COMMON, COMMON, 200),
            ("valid", STRONG, STRONG, 302),
        ):
            with self.subTest(case=label):
                user, invitation = self.invitation_for(f"invite-{label.replace(' ', '-')}")
                response = self.client.post(
                    reverse("accept_invitation", args=[invitation.token]),
                    {"password": password, "password_confirm": confirm},
                )
                self.assertEqual(response.status_code, expected_status)
                user.refresh_from_db()
                invitation.refresh_from_db()
                self.assertEqual(user.check_password(STRONG), label == "valid")
                self.assertEqual(invitation.is_open, label != "valid")
