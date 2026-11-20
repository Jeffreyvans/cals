"""Brand guard: one visible identity, with only internal names left behind.

The first test scans every readable source file for the old product spelling.
Anything that matches must be covered by an explicit allow-list entry below;
those entries are only for identifiers that cannot be renamed safely (environment
variables, cache keys, loggers, HTTP headers, migrations and similar internals).

The remaining tests exercise the surfaces that must read from ``config.brand``:
pages, HTML email, the PWA manifest, error pages, PDF attribution and the
per-institution white-label switch. Institution names, logos and colours must
stay untouched by any of it.
"""

from __future__ import annotations

import inspect
import json
import re
from datetime import date
from pathlib import Path
from unittest.mock import patch

from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.urls import reverse

from academics import documents as academic_documents
from admissions import documents as admission_documents
from config.brand import COMPANY_NAME, POWERED_BY, PRODUCT_NAME, SUPPORT_EMAIL, TAGLINE
from core.branding import primary_colour
from core.document_header import header_context, platform_attribution
from core.middleware import OldDomainRedirectMiddleware
from core.models import Institution
from finance import invoices, receipts, statements
from operations import documents as operation_documents

ROOT = Path(__file__).resolve().parents[2]

OLD_NAME = re.compile(
    r"(EduSphere|EduSpere|Eduspere|edusphere|EDUSPHERE|edu[-_ ]?sphere|sphereeedu)",
    re.I,
)

SKIP_DIRS = {
    ".git", ".pytest_cache", ".ruff_cache", "__pycache__", "logs", "media",
    "node_modules", "staticfiles", "venv", ".venv",
}
SKIP_EXTS = {
    ".ico", ".jpg", ".jpeg", ".map", ".pdf", ".png", ".pyc", ".sqlite3",
    ".woff", ".woff2", ".ttf", ".zip",
}

# Whole files whose old spelling is entirely internal. Historical migrations
# must never be rewritten; the error-handling JS and env tests deliberately
# exercise the old identifiers.
WHOLE_FILE_ALLOW = (
    r"^accounts/migrations/0009_user_is_platform_admin_user_password_set_from_env_and_more\.py$",
    r"^config/tests/test_brand\.py$",
    r"^config/tests/test_env\.py$",
    r"^platform_admin/migrations/0001_initial\.py$",
    r"^static/js/error-handling\.js$",
)

# (path restriction, allowed line pattern). A blank path restriction means the
# rule applies anywhere. Keep these as narrow as the identifier allows.
ALLOWED_LINES = (
    # Legacy environment variable names and fallbacks.
    (None, re.compile(r"EDUSPHERE_[A-Z0-9_*]+|EDUSPHERE_")),
    # Internal class names kept so URL names and imports stay stable.
    (None, re.compile(
        r"EduSphere(LoginView|LandingView|PasswordResetForm|"
        r"PasswordResetView|PasswordResetConfirmView)"
    )),
    # HTTP headers, browser globals, cookies, form storage and DOM ids.
    (None, re.compile(r"X-EduSphere-[A-Za-z*-]+")),
    (None, re.compile(r"x-edusphere-[a-z-]+")),
    (None, re.compile(r"window\.EDUSPHERE_ERRORS|window\.EdusphereErrors")),
    (None, re.compile(r"edusphere-error-banner")),
    # Cache keys, loggers, Celery app, DNS labels, bucket names, fixture
    # domains and template storage keys. All lower case and dot/colon/dash
    # separated: never a sentence shown to a user.
    (None, re.compile(r"edusphere[.:_-][A-Za-z0-9_.:/{}\s-]+")),
    (None, re.compile(r"[\"']edusphere[\"']")),
    (None, re.compile(r"`edusphere`|=edusphere\b|hooks/edusphere")),
)


def _path_allowed(rel_path: str) -> bool:
    return any(re.fullmatch(pattern, rel_path) for pattern in WHOLE_FILE_ALLOW)


def _line_allowed(line: str) -> bool:
    return any(pattern.search(line) for _path, pattern in ALLOWED_LINES)


class BrandGuardTests(TestCase):
    def test_old_product_name_only_appears_on_the_allow_list(self):
        offenders = []
        for path in ROOT.rglob("*"):
            rel = path.relative_to(ROOT).as_posix()
            if not path.is_file() or any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
                continue
            if path.suffix.lower() in SKIP_EXTS or _path_allowed(rel):
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for number, line in enumerate(lines, 1):
                if OLD_NAME.search(line) and not _line_allowed(line):
                    offenders.append(f"{rel}:{number}: {line.strip()[:160]}")
        self.assertEqual(offenders, [], "Old brand usage outside the allow-list")

    def test_brand_values_are_the_single_source_of_truth(self):
        from config.brand import values

        brand = values()
        self.assertEqual(brand["product_name"], PRODUCT_NAME)
        self.assertEqual(brand["company_name"], COMPANY_NAME)
        self.assertEqual(brand["tagline"], TAGLINE)
        self.assertEqual(brand["support_email"], SUPPORT_EMAIL)
        self.assertEqual(brand["powered_by"], POWERED_BY)
        self.assertIn(str(date.today().year), brand["copyright"])
        self.assertIn(COMPANY_NAME, brand["copyright"])

    def test_template_tags_work_without_a_request(self):
        html = render_to_string(
            "emails/generic.html",
            {"subject": "Hello", "body_html": "<p>Plain body</p>", "institution": None},
        )
        self.assertIn(PRODUCT_NAME, html)
        self.assertIn("Plain body", html)
        self.assertIn(COMPANY_NAME, html)

    def test_public_pages_and_login_use_the_brand(self):
        for url in (reverse("login"), reverse("about"), "/"):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, PRODUCT_NAME)
                self.assertContains(response, COMPANY_NAME)
                self.assertContains(response, SUPPORT_EMAIL)
                self.assertContains(response, '+263774573765')

    def test_about_page_makes_no_unverifiable_claims(self):
        response = self.client.get(reverse("about"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, COMPANY_NAME)
        for phrase in ("award-winning", "trusted by", "customer logos", "10,000"):
            self.assertNotContains(response, phrase, status_code=200)

    def test_manifest_is_generated_from_the_brand(self):
        response = self.client.get(reverse("manifest"))
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["name"], PRODUCT_NAME)
        self.assertEqual(data["short_name"], PRODUCT_NAME)
        self.assertIn(TAGLINE, data["description"])
        self.assertEqual(len(data["icons"]), 2)
        self.assertContains(self.client.get(reverse("login")), 'href="/manifest.json"')

    def test_error_page_shows_the_platform_name(self):
        response = self.client.get("/no-such-page-for-brand-tests/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, PRODUCT_NAME, status_code=404)

    def test_institution_email_keeps_its_name_with_a_via_brand_footer(self):
        institution = Institution(name="Riverside Academy", slug="riverside", type="secondary")
        html = render_to_string(
            "emails/generic.html",
            {"subject": "Hello", "body_html": "<p>Plain body</p>", "institution": institution},
        )
        self.assertIn("Riverside Academy", html)
        self.assertIn(f"via {PRODUCT_NAME}", html)
        self.assertIn(POWERED_BY, html)

    def test_white_label_switch_hides_attribution_everywhere(self):
        institution = Institution(
            name="Riverside Academy", slug="riverside", type="secondary",
            show_platform_branding=False,
        )
        self.assertEqual(platform_attribution(institution), "")
        html = render_to_string("includes/powered_by.html", {"institution": institution})
        self.assertNotIn(POWERED_BY, html)
        email = render_to_string(
            "emails/generic.html",
            {"subject": "Hello", "body_html": "<p>Plain body</p>", "institution": institution},
        )
        self.assertNotIn(f"via {PRODUCT_NAME}", email)
        self.assertNotIn(POWERED_BY, email)
        self.assertIn("Riverside Academy", email)

    def test_institution_branding_is_preserved(self):
        institution = Institution(
            name="Riverside Academy", slug="riverside", type="secondary",
            primary_color="#123456", show_platform_branding=True,
        )
        logo_url = "/media/institutions/logo.png"
        with patch("core.document_header.institution_logo_url", return_value=logo_url):
            context = header_context(institution)
        self.assertEqual(context["details"]["name"], "Riverside Academy")
        self.assertEqual(context["logo_url"], logo_url)
        self.assertEqual(primary_colour(institution), "#123456")
        self.assertEqual(platform_attribution(institution), POWERED_BY)

    def test_every_pdf_builder_uses_the_shared_attribution_helper(self):
        modules = (
            academic_documents, admission_documents, invoices, receipts,
            statements, operation_documents,
        )
        for module in modules:
            with self.subTest(module=module.__name__):
                source = inspect.getsource(module)
                self.assertIn("platform_attribution", source)

    def test_old_domain_redirects_permanently_to_site_url(self):
        request = RequestFactory().get("/old/path/?x=1", HTTP_HOST="old.example.com:8000")
        with self.settings(OLD_SITE_URL=["old.example.com"], SITE_URL="https://vanpere.example"):
            response = OldDomainRedirectMiddleware(lambda _request: None)(request)
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "https://vanpere.example/old/path/?x=1")
