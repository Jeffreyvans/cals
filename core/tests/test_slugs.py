"""Institution web addresses: validation, old-link redirects and domains.

A slug is a public address, so these tests cover the three promises the
platform makes: a slug is a plain safe word, a recently changed address keeps
working for 90 days, and a custom domain only takes effect after it has been
verified and configured on the host.
"""

from __future__ import annotations

from datetime import timedelta

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from core.models import (
    Institution,
    InstitutionSlugRedirect,
    record_slug_change,
    resolve_institution_slug,
)
from core.modules import set_module
from core.slugs import (
    MAX_SLUG_LENGTH,
    RESERVED_SLUGS,
    domain_verification_token,
    normalize_domain,
    normalize_slug,
    unique_slug,
    validate_slug,
)


class SlugValidationTests(TestCase):
    def test_normalize_turns_a_name_into_a_plain_slug(self):
        self.assertEqual(normalize_slug("Linde Net College"), "linde-net-college")
        self.assertEqual(normalize_slug("  A  B  "), "a-b")
        self.assertEqual(normalize_slug(""), "")
        self.assertEqual(len(normalize_slug("a" * 200)), MAX_SLUG_LENGTH)

    def test_reserved_words_are_refused(self):
        for word in sorted(RESERVED_SLUGS):
            with self.subTest(word=word):
                with self.assertRaisesMessage(ValidationError, "reserved"):
                    validate_slug(word)

    def test_invalid_characters_are_refused(self):
        for value in ("has space", "under_score", "dot.name", "-leading", "trailing-"):
            with self.subTest(value=value):
                with self.assertRaisesMessage(ValidationError, "small letters"):
                    validate_slug(value, normalize=False)

    def test_an_existing_institution_slug_is_refused(self):
        Institution.objects.create(name="Alpha", slug="alpha", type="college")
        with self.assertRaisesMessage(ValidationError, "already taken"):
            validate_slug("alpha")

    def test_an_institution_may_keep_its_own_slug(self):
        institution = Institution.objects.create(name="Alpha", slug="alpha", type="college")
        self.assertEqual(validate_slug("alpha", exclude=institution), "alpha")

    def test_a_recently_used_slug_is_reserved_while_its_redirect_lives(self):
        institution = Institution.objects.create(name="Alpha", slug="alpha-new", type="college")
        InstitutionSlugRedirect.objects.create(
            institution=institution,
            old_slug="alpha-old",
            expires_at=timezone.now() + timedelta(days=10),
        )
        with self.assertRaisesMessage(ValidationError, "still redirecting"):
            validate_slug("alpha-old")
        # Its own institution may still move back to the old address.
        self.assertEqual(validate_slug("alpha-old", exclude=institution), "alpha-old")

    def test_an_expired_redirect_no_longer_reserves_a_slug(self):
        institution = Institution.objects.create(name="Alpha", slug="alpha-new", type="college")
        InstitutionSlugRedirect.objects.create(
            institution=institution,
            old_slug="alpha-old",
            expires_at=timezone.now() - timedelta(days=1),
        )
        self.assertEqual(validate_slug("alpha-old"), "alpha-old")

    def test_unique_slug_adds_a_suffix_instead_of_failing(self):
        Institution.objects.create(name="Alpha", slug="alpha", type="college")
        self.assertEqual(unique_slug("Alpha"), "alpha-2")
        Institution.objects.create(name="Alpha Two", slug="alpha-2", type="college")
        self.assertEqual(unique_slug("Alpha"), "alpha-3")

    def test_unique_slug_never_returns_a_reserved_word(self):
        self.assertNotIn(unique_slug("Admin"), RESERVED_SLUGS)


class DomainValidationTests(TestCase):
    def test_a_hostname_is_accepted_and_lowercased(self):
        self.assertEqual(normalize_domain(" Learn.Example.ORG. "), "learn.example.org")

    def test_a_url_is_reduced_to_its_host(self):
        self.assertEqual(
            normalize_domain("https://learn.example.org/some/path?q=1"), "learn.example.org"
        )

    def test_invalid_domains_are_refused(self):
        for value in ("localhost", "example", "has space.com", "https://", ""):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    normalize_domain(value)

    def test_verification_tokens_are_unique_and_long_enough(self):
        first = domain_verification_token()
        self.assertEqual(len(first), 48)
        self.assertNotEqual(first, domain_verification_token())


class SlugRedirectTests(TestCase):
    def setUp(self):
        self.institution = Institution.objects.create(
            name="Alpha College", slug="alpha", type="college", public_page_enabled=True
        )

    def test_a_changed_slug_keeps_the_old_address_for_ninety_days(self):
        record_slug_change(self.institution, "old-alpha", "alpha")
        row = InstitutionSlugRedirect.objects.get(old_slug="old-alpha")
        self.assertEqual(row.institution, self.institution)
        self.assertFalse(row.is_expired)
        self.assertAlmostEqual((row.expires_at - timezone.now()).days, 89, delta=1)

    def test_an_unchanged_slug_creates_no_redirect(self):
        record_slug_change(self.institution, "alpha", "alpha")
        self.assertFalse(InstitutionSlugRedirect.objects.exists())

    def test_current_and_recent_slugs_both_resolve(self):
        self.assertEqual(resolve_institution_slug("alpha"), self.institution)
        record_slug_change(self.institution, "old-alpha", "alpha")
        self.assertEqual(resolve_institution_slug("old-alpha"), self.institution)
        self.assertIsNone(resolve_institution_slug("never-used"))

    def test_expired_redirects_stop_resolving(self):
        InstitutionSlugRedirect.objects.create(
            institution=self.institution,
            old_slug="old-alpha",
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        self.assertIsNone(resolve_institution_slug("old-alpha"))

    def test_an_inactive_institution_does_not_resolve(self):
        record_slug_change(self.institution, "old-alpha", "alpha")
        self.institution.is_active = False
        self.institution.save(update_fields=["is_active"])
        self.assertIsNone(resolve_institution_slug("alpha"))
        self.assertIsNone(resolve_institution_slug("old-alpha"))

    def test_stale_redirects_are_cleaned_up(self):
        InstitutionSlugRedirect.objects.create(
            institution=self.institution,
            old_slug="very-old",
            expires_at=timezone.now() - timedelta(days=1),
        )
        record_slug_change(self.institution, "old-alpha", "alpha")
        self.assertFalse(InstitutionSlugRedirect.objects.filter(old_slug="very-old").exists())


class OldAddressPagesTests(TestCase):
    def setUp(self):
        self.institution = Institution.objects.create(
            name="Alpha College", slug="alpha", type="college", public_page_enabled=True
        )
        record_slug_change(self.institution, "old-alpha", "alpha")

    def test_the_public_page_follows_an_old_slug(self):
        response = self.client.get("/i/old-alpha/")
        self.assertRedirects(response, "/i/alpha/", fetch_redirect_response=False)

    def test_the_login_page_follows_an_old_slug(self):
        response = self.client.get("/i/old-alpha/login/")
        self.assertRedirects(response, "/i/alpha/login/", fetch_redirect_response=False)

    def test_the_login_redirect_keeps_the_query_string(self):
        response = self.client.get("/i/old-alpha/login/?next=/dashboard/")
        self.assertEqual(response["Location"], "/i/alpha/login/?next=/dashboard/")

    def test_an_expired_old_address_is_a_404(self):
        InstitutionSlugRedirect.objects.filter(old_slug="old-alpha").update(
            expires_at=timezone.now() - timedelta(days=1)
        )
        self.assertEqual(self.client.get("/i/old-alpha/").status_code, 404)
        self.assertEqual(self.client.get("/i/old-alpha/login/").status_code, 404)


class CustomDomainHostTests(TestCase):
    """A custom domain maps to an institution only once it is verified."""

    def setUp(self):
        cache.clear()
        self.institution = Institution.objects.create(
            name="Alpha College", slug="alpha", type="college"
        )
        set_module(self.institution, "white_label", True)

    def _request_institution(self):
        response = self.client.get("/login/", HTTP_HOST="learn.alpha.example")
        return getattr(response.wsgi_request, "institution", None)

    def test_a_verified_enabled_domain_maps_to_its_institution(self):
        self.institution.custom_domain = "learn.alpha.example"
        self.institution.custom_domain_verified = True
        self.institution.custom_domain_enabled = True
        self.institution.save()
        cache.clear()
        self.assertEqual(self._request_institution().pk, self.institution.pk)

    def test_an_unverified_domain_is_ignored(self):
        self.institution.custom_domain = "learn.alpha.example"
        self.institution.custom_domain_enabled = True
        self.institution.save()
        cache.clear()
        self.assertIsNone(self._request_institution())

    def test_a_disabled_domain_is_ignored(self):
        self.institution.custom_domain = "learn.alpha.example"
        self.institution.custom_domain_verified = True
        self.institution.custom_domain_enabled = False
        self.institution.save()
        cache.clear()
        self.assertIsNone(self._request_institution())

    def test_an_unknown_domain_is_ignored(self):
        self.assertIsNone(self._request_institution())
