"""Part 3: a sign-in page branded for one institution at ``/i/<slug>/login/``.

The page shows the institution's logo, name and tagline, and it only accepts
accounts that belong to that institution. These tests cover the page, the
institution check, the shared lockout behaviour and the shareable link.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from core.models import Institution
from core.roles import ensure_roles

User = get_user_model()
STRONG = "Str0ngPassw0rd"


class InstitutionLoginBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alpha = Institution.objects.create(
            name="Alpha College",
            slug="alpha",
            type="college",
            tagline="Learn. Serve. Lead.",
            public_registration=True,
            primary_color="#123456",
            accent_color="#abcdef",
            contact_email="office@alpha.test",
            contact_phone="+263771000111",
        )
        cls.beta = Institution.objects.create(
            name="Beta College", slug="beta", type="college",
            public_registration=False,
        )
        cls.alpha_user = User.objects.create_user(
            "alphauser", password=STRONG, role="student", institution=cls.alpha,
            email="alphauser@example.test")
        cls.beta_user = User.objects.create_user(
            "betauser", password=STRONG, role="student", institution=cls.beta)
        cls.superuser = User.objects.create_superuser(
            "root", password=STRONG, email="root@example.test")

    def setUp(self):
        cache.clear()

    def url(self, slug="alpha"):
        return reverse("institution_login", args=[slug])


class InstitutionLoginPageTests(InstitutionLoginBase):
    def test_the_page_is_branded_for_the_institution(self):
        resp = self.client.get(self.url())
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "css/public.css")
        self.assertContains(resp, "Alpha College")
        self.assertContains(resp, "Learn. Serve. Lead.")
        self.assertContains(resp, "brand-badge")
        # No logo is set, so the initials are the fallback.
        self.assertContains(resp, "AC")

    def test_the_institution_colours_stay_off_the_public_page(self):
        resp = self.client.get(self.url())
        self.assertNotContains(resp, "#123456")
        self.assertNotContains(resp, "#abcdef")

    def test_the_page_offers_registration_when_it_is_switched_on(self):
        resp = self.client.get(self.url())
        self.assertContains(resp, reverse("register_choice"))

    def test_an_unknown_institution_is_a_404(self):
        resp = self.client.get(self.url("does-not-exist"))
        self.assertEqual(resp.status_code, 404)

    def test_an_inactive_institution_is_a_404(self):
        Institution.objects.create(name="Closed", slug="closed", is_active=False)
        resp = self.client.get(self.url("closed"))
        self.assertEqual(resp.status_code, 404)

    def test_the_public_institution_page_links_to_the_login(self):
        resp = self.client.get(reverse("institution_public", args=["alpha"]))
        self.assertContains(resp, reverse("institution_login", args=["alpha"]))


class InstitutionLoginFormTests(InstitutionLoginBase):
    def test_a_user_of_the_institution_can_sign_in(self):
        resp = self.client.post(
            self.url(), {"username": "alphauser", "password": STRONG}
        )
        self.assertRedirects(
            resp, reverse("dashboard"), fetch_redirect_response=False
        )
        self.assertEqual(
            int(self.client.session["_auth_user_id"]), self.alpha_user.pk
        )

    def test_a_user_of_another_institution_cannot_sign_in(self):
        resp = self.client.post(
            self.url(), {"username": "betauser", "password": STRONG}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertContains(resp, "Wrong email, username or password")

    def test_a_superuser_can_sign_in_on_any_institution_page(self):
        resp = self.client.post(
            self.url(), {"username": "root", "password": STRONG}
        )
        self.assertRedirects(
            resp, reverse("dashboard"), fetch_redirect_response=False
        )

    def test_a_wrong_password_is_refused(self):
        resp = self.client.post(
            self.url(), {"username": "alphauser", "password": "not-the-password"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_works_even_when_public_registration_is_off(self):
        resp = self.client.post(
            self.url("beta"), {"username": "betauser", "password": STRONG}
        )
        self.assertRedirects(
            resp, reverse("dashboard"), fetch_redirect_response=False
        )

    def test_the_next_parameter_is_honoured(self):
        resp = self.client.post(
            self.url() + "?next=/users/",
            {"username": "alphauser", "password": STRONG},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/users/")

    def test_a_signed_in_visitor_is_sent_to_the_dashboard(self):
        self.client.force_login(self.alpha_user)
        resp = self.client.get(self.url())
        self.assertRedirects(
            resp, reverse("dashboard"), fetch_redirect_response=False
        )

    def test_a_failed_attempt_is_counted(self):
        self.client.post(
            self.url(), {"username": "alphauser", "password": "not-the-password"}
        )
        self.alpha_user.refresh_from_db()
        self.assertEqual(self.alpha_user.failed_login_count, 1)

    def test_an_inactive_account_cannot_sign_in(self):
        User.objects.filter(pk=self.alpha_user.pk).update(is_active=False)
        resp = self.client.post(
            self.url(), {"username": "alphauser", "password": STRONG}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)


class InstitutionLoginSettingsTests(InstitutionLoginBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.roles = ensure_roles(cls.alpha)
        cls.admin = User.objects.create_user(
            "alphaadmin", password=STRONG, role="admin", institution=cls.alpha
        )
        cls.admin.primary_role = cls.roles["institution_admin"]
        cls.admin.save(update_fields=["primary_role"])

    def test_the_settings_screen_shows_the_shareable_link(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("institution_settings"))
        self.assertContains(resp, reverse("institution_login", args=["alpha"]))
        self.assertContains(resp, "Institution sign-in link")
