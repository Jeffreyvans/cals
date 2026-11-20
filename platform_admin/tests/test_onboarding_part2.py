"""Part 2 of institution onboarding: shareable links and first sign-in.

These tests prove that the platform admin can hand an institution everything it
needs to start: a branded sign-in link, a sign-up link, an optional custom
domain and a short guided setup after the first administrator signs in. Every
screen stays inside the platform area and never exposes institution records.
"""

from __future__ import annotations

import json

from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from accounts.models import SignupLink
from config.brand import PRODUCT_NAME, SUPPORT_EMAIL
from core.models import (
    AuditEntry,
    InstitutionSlugRedirect,
    OutboundMessage,
)
from platform_admin.models import InstitutionOnboarding, PlatformAuditLog
from platform_admin.onboarding import add_administrator

from .base import STRONG_PASSWORD, PlatformTestBase


def detail_payload(**overrides):
    data = {
        "action": "edit",
        "name": "Alpha College",
        "slug": "alpha-platform",
        "type": "college",
        "address": "",
        "contact_email": "office@alpha.example",
        "contact_phone": "",
        "website": "",
        "access_grant_max_hours": 24,
        "public_page_enabled": "on",
    }
    data.update(overrides)
    return data


class LoginLinkPanelTests(PlatformTestBase):
    def setUp(self):
        super().setUp()
        cache.clear()

    def test_the_panel_shows_a_copyable_link_qr_whatsapp_and_status(self):
        self.login(self.main)
        page = self.client.get(reverse("platform_admin:institution_detail", args=[self.inst.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "/i/alpha-platform/login/")
        self.assertContains(page, "Copy")
        self.assertContains(page, "WhatsApp")
        self.assertContains(page, "Enabled")
        self.assertContains(
            page, reverse("platform_admin:institution_login_qr", args=[self.inst.pk])
        )

    def test_an_inactive_institution_shows_the_link_as_disabled(self):
        self.inst.is_active = False
        self.inst.save(update_fields=["is_active"])
        self.login(self.main)
        page = self.client.get(reverse("platform_admin:institution_detail", args=[self.inst.pk]))
        self.assertContains(page, "Disabled")

    def test_the_qr_endpoint_returns_a_png(self):
        self.login(self.main)
        response = self.client.get(
            reverse("platform_admin:institution_login_qr", args=[self.inst.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertTrue(response.content.startswith(b"\x89PNG"))

    def test_emailing_the_link_goes_through_the_central_email_function(self):
        self.login(self.main)
        response = self.client.post(
            reverse("platform_admin:institution_detail", args=[self.inst.pk]),
            {"action": "email_login_link", "user": self.inst_admin.pk},
        )
        self.assertEqual(response.status_code, 302)
        message = OutboundMessage.objects.filter(
            recipient=self.inst_admin.email, subject__icontains="Sign in"
        ).first()
        self.assertIsNotNone(message)
        self.assertEqual(message.status, OutboundMessage.Status.SENT)
        self.assertIn("/i/alpha-platform/login/", message.body)
        self.assertIn("+263774573765", message.body)
        self.assertIn(SUPPORT_EMAIL, message.body)
        self.assertTrue(
            PlatformAuditLog.objects.filter(action="institution.login_link_emailed").exists()
        )

    def test_non_platform_users_cannot_reach_the_link_tools(self):
        self.client.force_login(self.inst_admin)
        for name in ("institution_login_qr", "institution_signup_link", "institution_domain"):
            with self.subTest(name=name):
                response = self.client.get(
                    reverse(f"platform_admin:{name}", args=[self.inst.pk])
                )
                self.assertEqual(response.status_code, 404)

    def test_platform_staff_without_the_right_permission_get_403(self):
        self.login(self.support)
        response = self.client.get(
            reverse("platform_admin:institution_signup_link", args=[self.inst.pk])
        )
        self.assertEqual(response.status_code, 403)


class SlugEditingTests(PlatformTestBase):
    def setUp(self):
        super().setUp()
        cache.clear()

    def test_editing_the_slug_keeps_the_old_address_alive(self):
        self.login(self.main)
        url = reverse("platform_admin:institution_detail", args=[self.inst.pk])
        response = self.client.post(url, detail_payload(slug="alpha-renamed"))
        self.assertEqual(response.status_code, 302)
        self.inst.refresh_from_db()
        self.assertEqual(self.inst.slug, "alpha-renamed")
        self.assertTrue(
            InstitutionSlugRedirect.objects.filter(
                institution=self.inst, old_slug="alpha-platform"
            ).exists()
        )
        self.assertIsNotNone(
            PlatformAuditLog.objects.filter(action="institution.edited").first()
        )

    def test_a_reserved_slug_is_refused(self):
        self.login(self.main)
        url = reverse("platform_admin:institution_detail", args=[self.inst.pk])
        response = self.client.post(url, detail_payload(slug="platform"))
        self.assertEqual(response.status_code, 200)
        self.inst.refresh_from_db()
        self.assertEqual(self.inst.slug, "alpha-platform")
        self.assertContains(response, "reserved")

    def test_another_institutions_slug_is_refused(self):
        self.login(self.main)
        url = reverse("platform_admin:institution_detail", args=[self.inst.pk])
        response = self.client.post(url, detail_payload(slug="beta-platform"))
        self.assertEqual(response.status_code, 200)
        self.inst.refresh_from_db()
        self.assertEqual(self.inst.slug, "alpha-platform")
        self.assertContains(response, "already taken")


class PlatformSignupLinkTests(PlatformTestBase):
    def test_a_platform_link_is_marked_and_obeys_the_normal_rules(self):
        self.login(self.main)
        url = reverse("platform_admin:institution_signup_link", args=[self.inst.pk])
        response = self.client.post(url, {
            "label": "Term 1 intake",
            "kind": "student",
            "expires_days": 10,
            "max_uses": 5,
        })
        self.assertEqual(response.status_code, 200)
        link = SignupLink.objects.get(institution=self.inst, label="Term 1 intake")
        self.assertTrue(link.created_by_platform_admin)
        self.assertEqual(link.kind, SignupLink.Kind.STUDENT)
        self.assertEqual(link.max_uses, 5)
        self.assertIsNotNone(link.expires_at)
        self.assertTrue(link.is_open)
        self.assertContains(response, link.url())
        self.assertIsNotNone(
            InstitutionOnboarding.objects.get(institution=self.inst).signup_links_created_at
        )
        self.assertTrue(
            PlatformAuditLog.objects.filter(action="institution.signup_link_created").exists()
        )

    def test_the_sign_up_page_accepts_the_link_at_this_institution(self):
        self.login(self.main)
        response = self.client.post(
            reverse("platform_admin:institution_signup_link", args=[self.inst.pk]),
            {"label": "Staff welcome", "kind": "staff"},
        )
        self.assertEqual(response.status_code, 200)
        link = SignupLink.objects.get(institution=self.inst, label="Staff welcome")
        page = self.client.get(link.path())
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Staff welcome")

    def test_resending_the_welcome_email_still_uses_the_shared_queue(self):
        admin = add_administrator(
            self.inst,
            full_name="Resume Admin",
            email="resume@alpha.example",
            access_mode="temporary_password",
            actor=self.main,
        ).user
        welcome = OutboundMessage.objects.filter(
            recipient=admin.email, subject__icontains=f"Welcome to {PRODUCT_NAME}"
        )
        self.assertEqual(welcome.count(), 1)

        self.login(self.main)
        response = self.client.post(
            reverse("platform_admin:institution_detail", args=[self.inst.pk]),
            {"action": "resend_welcome", "user": admin.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(welcome.count(), 2)
        self.assertEqual(
            InstitutionOnboarding.objects.get(institution=self.inst).welcome_email_count, 2
        )
        self.assertTrue(
            PlatformAuditLog.objects.filter(action="institution.welcome_email_resent").exists()
        )


class CustomDomainPanelTests(PlatformTestBase):
    def setUp(self):
        super().setUp()
        cache.clear()

    def test_a_new_institution_has_no_custom_domain(self):
        self.assertFalse(self.inst.custom_domain_enabled)
        self.assertFalse(self.inst.custom_domain_verified)
        self.assertEqual(self.inst.custom_domain, "")

    def test_saving_a_domain_gives_verification_steps_and_leaves_it_off(self):
        self.login(self.main)
        url = reverse("platform_admin:institution_domain", args=[self.inst.pk])
        response = self.client.post(url, {
            "action": "save", "custom_domain": "Learn.Alpha.Example",
        })
        self.assertEqual(response.status_code, 302)
        self.inst.refresh_from_db()
        self.assertEqual(self.inst.custom_domain, "learn.alpha.example")
        self.assertTrue(self.inst.custom_domain_token)
        self.assertFalse(self.inst.custom_domain_verified)
        self.assertFalse(self.inst.custom_domain_enabled)

        page = self.client.get(url)
        self.assertContains(page, "_edusphere.learn.alpha.example")
        self.assertContains(page, self.inst.custom_domain_token)
        self.assertContains(page, "ALLOWED_HOSTS")
        self.assertContains(page, "CSRF_TRUSTED_ORIGINS")

    def test_verification_needs_an_explicit_confirmation(self):
        self.login(self.main)
        url = reverse("platform_admin:institution_domain", args=[self.inst.pk])
        self.client.post(url, {"action": "save", "custom_domain": "learn.alpha.example"})
        self.client.post(url, {"action": "verify"})
        self.inst.refresh_from_db()
        self.assertFalse(self.inst.custom_domain_enabled)

        self.client.post(url, {"action": "verify", "confirm": "on"})
        self.inst.refresh_from_db()
        self.assertTrue(self.inst.custom_domain_verified)
        self.assertTrue(self.inst.custom_domain_enabled)
        self.assertIsNotNone(self.inst.custom_domain_verified_at)
        self.assertTrue(
            PlatformAuditLog.objects.filter(action="institution.custom_domain_verified").exists()
        )

    def test_disabling_a_domain_keeps_the_record_but_stops_host_mapping(self):
        self.login(self.main)
        url = reverse("platform_admin:institution_domain", args=[self.inst.pk])
        self.client.post(url, {"action": "save", "custom_domain": "learn.alpha.example"})
        self.client.post(url, {"action": "verify", "confirm": "on"})
        response = self.client.post(url, {"action": "disable"})
        self.assertEqual(response.status_code, 302)
        self.inst.refresh_from_db()
        self.assertEqual(self.inst.custom_domain, "learn.alpha.example")
        self.assertFalse(self.inst.custom_domain_enabled)
        self.assertTrue(self.inst.custom_domain_verified)
        cache.clear()
        # Sign out first: an authenticated platform user carries its own
        # institution, which must not be confused with host-based mapping.
        self.client.logout()
        mapped = self.client.get("/login/", HTTP_HOST="learn.alpha.example").wsgi_request.institution
        self.assertIsNone(mapped)

    def test_an_invalid_domain_is_refused(self):
        self.login(self.main)
        url = reverse("platform_admin:institution_domain", args=[self.inst.pk])
        response = self.client.post(url, {"action": "save", "custom_domain": "not a domain"})
        self.assertEqual(response.status_code, 200)
        self.inst.refresh_from_db()
        self.assertEqual(self.inst.custom_domain, "")


class GuidedSetupTests(PlatformTestBase):
    """The handoff from a real first sign-in into the institution setup."""

    def setUp(self):
        super().setUp()
        # The base fixtures predate onboarding, so mark this administrator as
        # platform-provisioned to mirror a real newly created institution.
        row, _created = InstitutionOnboarding.objects.get_or_create(institution=self.inst)
        if row.first_admin_created_at is None:
            row.first_admin_created_at = timezone.now()
            row.save(update_fields=["first_admin_created_at", "updated"])

    def _sign_in(self, user=None):
        user = user or self.inst_admin
        return self.client.post(
            reverse("login"), {"username": user.username, "password": STRONG_PASSWORD}
        )

    def test_a_real_first_sign_in_opens_the_guided_setup(self):
        response = self._sign_in()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/dashboard/")

        redirected = self.client.get("/dashboard/")
        self.assertEqual(redirected["Location"], reverse("institution_setup"))
        page = self.client.get(reverse("institution_setup"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Choose a password")
        row = InstitutionOnboarding.objects.get(institution=self.inst)
        self.assertIsNotNone(row.first_admin_login_at)
        self.assertIsNotNone(row.setup_started_at)

    def test_force_login_does_not_start_the_guided_setup(self):
        # Test helpers fire the login signal but must not be sent to setup.
        self.client.force_login(self.inst_admin)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(
            reverse("institution_setup"), response.get("Location", "")
        )

    def test_a_legacy_administrator_is_not_sent_to_setup(self):
        # An institution that existed before onboarding must keep its normal
        # sign-in behaviour on the first login after the upgrade.
        InstitutionOnboarding.objects.filter(institution=self.inst).delete()
        response = self._sign_in()
        self.assertEqual(response["Location"], "/dashboard/")
        self.assertEqual(self.client.get("/dashboard/").status_code, 200)

    def test_a_second_institution_never_sees_the_first_setup(self):
        self.client.force_login(self.other_admin)
        page = self.client.get(reverse("institution_setup"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Beta College")
        self.assertNotContains(page, "Alpha College")
        self.assertFalse(
            InstitutionOnboarding.objects.filter(
                institution=self.inst, setup_started_at__isnull=False
            ).exists()
        )

    def test_platform_staff_cannot_open_institution_setup(self):
        self.login(self.main)
        response = self.client.get(reverse("institution_setup"))
        self.assertEqual(response.status_code, 403)

    def test_a_non_admin_institution_user_cannot_open_setup(self):
        self.client.force_login(self.teacher)
        response = self.client.get(reverse("institution_setup"))
        self.assertEqual(response.status_code, 403)

    def test_the_five_steps_can_be_completed(self):
        self._sign_in()
        self.client.get(reverse("institution_setup"))

        response = self.client.post(reverse("institution_setup"), {
            "action": "next",
            "password": STRONG_PASSWORD,
            "password_confirm": STRONG_PASSWORD,
        })
        self.assertEqual(response.status_code, 302)
        response = self.client.post(reverse("institution_setup"), {"action": "next"})
        self.assertEqual(response.status_code, 302)
        response = self.client.post(reverse("institution_setup"), {
            "action": "next",
            "tagline": "Learn with us",
            "about": "A college for everyone.",
            "contact_email": "office@alpha.example",
            "contact_phone": "+263771111111",
            "website": "https://alpha.example",
            "address": "12 Main Road",
            "primary_color": "#3b2412",
            "accent_color": "#c57b46",
        })
        self.assertEqual(response.status_code, 302)
        response = self.client.post(reverse("institution_setup"), {
            "action": "next",
            "year_name": "2026",
            "year_start": "2026-01-01",
            "year_end": "2026-12-31",
            "term_name": "Term 1",
            "term_start": "2026-01-01",
            "term_end": "2026-04-30",
        })
        self.assertEqual(response.status_code, 302)

        response = self.client.post(reverse("institution_setup"), {
            "action": "next", "label": "First intake", "kind": "any",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your sign-up link is ready")
        link = SignupLink.objects.get(institution=self.inst, label="First intake")
        self.assertFalse(link.created_by_platform_admin)

        response = self.client.post(reverse("institution_setup"), {"action": "finish"})
        self.assertEqual(response["Location"], reverse("dashboard"))
        row = InstitutionOnboarding.objects.get(institution=self.inst)
        self.assertIsNotNone(row.setup_completed_at)
        self.assertIsNotNone(row.signup_links_created_at)
        self.assertTrue(
            AuditEntry.objects.filter(action="institution.setup_completed").exists()
        )

    def test_audits_never_store_a_signup_token_or_a_password(self):
        self.login(self.main)
        self.client.post(
            reverse("platform_admin:institution_signup_link", args=[self.inst.pk]),
            {"label": "Secret audit", "kind": "any"},
        )
        self.client.post(
            reverse("platform_admin:institution_detail", args=[self.inst.pk]),
            {"action": "email_login_link", "user": self.inst_admin.pk},
        )
        link = SignupLink.objects.get(institution=self.inst, label="Secret audit")
        payload = json.dumps(list(
            PlatformAuditLog.objects.values("detail", "before", "after")
        ))
        self.assertNotIn(str(link.token), payload)
        self.assertNotIn(STRONG_PASSWORD, payload)
        self.assertNotIn("needs_institution_setup", payload)
