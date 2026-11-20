"""Tests for categorised errors, pages, logs, JSON, offline and retries."""

import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, SuspiciousOperation, ValidationError
from django.db import IntegrityError, OperationalError
from django.db.models.deletion import ProtectedError
from django.http import Http404
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from core.error_catalogue import ERROR_CATALOGUE, ERRORS_BY_CODE
from core.errors import can_view_error_details, classify_exception, error_json, render_error
from core.idempotency import begin, finish, new_key
from core.models import ErrorRecord, Institution
from platform_admin.tests.base import PlatformTestBase

User = get_user_model()
BASE_DIR = Path(__file__).resolve().parents[2]


class CatalogueTests(TestCase):
    def test_every_code_is_complete_and_categorised(self):
        self.assertEqual(len(ERROR_CATALOGUE), 30)
        self.assertEqual(len(ERRORS_BY_CODE), 30)
        for spec in ERROR_CATALOGUE:
            with self.subTest(code=spec.code):
                self.assertIn(spec.category, {"NETWORK", "SERVER", "DATABASE", "ACCESS", "INPUT", "EXTERNAL"})
                self.assertTrue(spec.icon)
                self.assertIn(spec.category_label, spec.title)
                self.assertIn(spec.code, spec.headline)
                self.assertTrue(spec.explanation)
                self.assertTrue(spec.action)
                self.assertGreaterEqual(len(spec.steps), 2)
                self.assertIn(spec.fixed_by, {"user", "school administrator", "platform admin", "developer"})
                self.assertGreaterEqual(spec.default_status, 400)

    def test_shared_codes_include_every_requested_code(self):
        expected = {
            "NET-OFFLINE", "NET-SLOW", "NET-TIMEOUT", "NET-UNREACHABLE",
            "SRV-500", "SRV-503", "SRV-504", "SRV-429", "SRV-MAINT",
            "DB-DOWN", "DB-CONFLICT", "DB-PROTECTED",
            "AUTH-REQUIRED", "AUTH-EXPIRED", "AUTH-FAILED", "AUTH-LOCKED",
            "AUTH-UNVERIFIED", "AUTH-2FA", "PERM-DENIED", "ORG-SUSPENDED",
            "NOT-FOUND", "CSRF",
            "INPUT-INVALID", "FILE-REJECTED", "IMPORT-ROWS",
            "EXT-EMAIL", "EXT-PAY", "EXT-STORAGE", "EXT-FISCAL", "EXT-HOOK",
        }
        self.assertEqual(set(ERRORS_BY_CODE), expected)


class ClassifyExceptionTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.request = self.factory.get("/some/page/")
        self.request.user = AnonymousUser()

    def test_mappings(self):
        cases = [
            (OperationalError("db gone"), "DB-DOWN"),
            (IntegrityError("duplicate"), "DB-CONFLICT"),
            (ProtectedError("in use", []), "DB-PROTECTED"),
            (requests.exceptions.Timeout("slow"), "NET-TIMEOUT"),
            (requests.exceptions.ConnectionError("dns"), "NET-UNREACHABLE"),
            (ValidationError("Enter a number"), "INPUT-INVALID"),
            (ValidationError("The logo is 4.2 MB"), "FILE-REJECTED"),
            (SuspiciousOperation("bad upload path"), "INPUT-INVALID"),
            (PermissionDenied("You cannot do that"), "PERM-DENIED"),
            (PermissionDenied("CSRF Failed: token expired"), "CSRF"),
            (Http404("missing"), "NOT-FOUND"),
        ]
        for exc, expected in cases:
            with self.subTest(exc=type(exc).__name__, expected=expected):
                self.assertEqual(classify_exception(exc, self.request).code, expected)

    def test_suspicious_file_operation(self):
        from django.core.exceptions import SuspiciousFileOperation

        exc = SuspiciousFileOperation("outside media root")
        self.assertEqual(classify_exception(exc, self.request).code, "FILE-REJECTED")

    def test_suspended_institution_overrides_permission_denied(self):
        from types import SimpleNamespace

        self.request.user = SimpleNamespace(
            is_authenticated=True,
            institution=SimpleNamespace(suspended_at=timezone.now(), is_active=False),
        )
        spec = classify_exception(PermissionDenied("no access"), self.request)
        self.assertEqual(spec.code, "ORG-SUSPENDED")

    def test_status_fallback(self):
        for status, code in [
            (400, "INPUT-INVALID"), (401, "AUTH-REQUIRED"), (403, "PERM-DENIED"),
            (404, "NOT-FOUND"), (408, "NET-TIMEOUT"), (409, "DB-CONFLICT"),
            (423, "AUTH-LOCKED"), (429, "SRV-429"), (500, "SRV-500"),
            (503, "SRV-503"), (504, "SRV-504"),
        ]:
            with self.subTest(status=status):
                self.assertEqual(classify_exception(None, self.request, status=status).code, code)


class ErrorPageTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user("page-reader", password="Str0ngPass!2026")

    def _page(self, code, user=None, exception=None):
        request = self.factory.get("/broken/page/")
        request.user = user or self.user
        return render_error(request, code, exception=exception)

    def test_every_category_renders_full_guidance_and_retry(self):
        for spec in ERROR_CATALOGUE:
            with self.subTest(code=spec.code):
                response = self._page(spec.code)
                html = response.content.decode()
                self.assertEqual(response.status_code, spec.default_status)
                self.assertIn(escape(spec.category_label), html)
                self.assertIn(spec.code, html)
                self.assertIn(escape(spec.headline), html)
                self.assertIn(escape(spec.explanation), html)
                for step in spec.steps:
                    self.assertIn(escape(step), html)
                self.assertIn("Try again", html)
                self.assertIn("Go back", html)
                self.assertRegex(html, r"ERR-[0-9A-F]{6}")
                self.assertIn("Copy", html)
                self.assertIn("Technical details", html)
                self.assertIn("+263774573765", html)
                self.assertIn("vanhuvaonejeffrey@gmail.com", html)

    def test_technical_details_hidden_from_normal_users(self):
        html = self._page("SRV-500", exception=RuntimeError("secret-token-should-not-appear")).content.decode()
        self.assertNotIn("Exception class", html)
        self.assertNotIn("secret-token-should-not-appear", html)
        self.assertNotIn("Traceback", html)
        self.assertIn("HTTP status", html)
        self.assertIn("Request id", html)

    def test_technical_details_shown_to_platform_admin(self):
        self.user.is_platform_admin = True
        html = self._page("SRV-500", exception=RuntimeError("classified-fault")).content.decode()
        self.assertIn("Exception class", html)
        self.assertIn("RuntimeError", html)
        self.assertNotIn("Traceback", html)

    def test_no_secrets_or_sql_in_any_response(self):
        html = self._page(
            "DB-DOWN",
            exception=RuntimeError("password=hunter2 SELECT * FROM users API_KEY=abcdefghijklmnop"),
        ).content.decode()
        for secret in ("hunter2", "SELECT * FROM users", "abcdefghijklmnop", "Traceback"):
            self.assertNotIn(secret, html)

    def test_error_record_stores_category_and_code(self):
        response = self._page("EXT-EMAIL")
        html = response.content.decode()
        reference = re.search(r"<code data-error-reference>(ERR-[0-9A-F]{6})</code>", html).group(1)
        record = ErrorRecord.objects.get(reference=reference)
        self.assertEqual(record.category, "EXTERNAL")
        self.assertEqual(record.code, "EXT-EMAIL")
        self.assertEqual(record.next_steps[0], "Copy the one-time link shown on this page.")


class JsonErrorTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.request = self.factory.post("/api/thing/")
        self.request.user = AnonymousUser()

    def test_json_error_matches_page_catalogue(self):
        response = error_json(self.request, "EXT-EMAIL")
        body = json.loads(response.content)
        spec = ERRORS_BY_CODE["EXT-EMAIL"]
        self.assertEqual(response.status_code, 502)
        self.assertEqual(body["code"], "EXT-EMAIL")
        self.assertEqual(body["category"], "EXTERNAL")
        self.assertEqual(body["title"], spec.title)
        self.assertEqual(body["next_steps"], list(spec.steps))
        self.assertIn(spec.category_label, body["detail"])
        self.assertNotIn("technical", body)  # extra internals need permission

    def test_drf_handler_adds_codes_without_dropping_detail(self):
        from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied
        from rest_framework.test import APIRequestFactory

        from core.errors import api_exception_handler

        request = APIRequestFactory().get("/api/thing/")
        request.user = AnonymousUser()
        response = api_exception_handler(
            DRFPermissionDenied("nope"), {"request": request, "view": object()}
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "PERM-DENIED")
        self.assertIn("Access problem", response.data["detail"])


class MiddlewareAndOfflineTests(TestCase):
    def test_404_uses_categorised_page(self):
        response = self.client.get("/this-page-does-not-exist/")
        self.assertEqual(response.status_code, 404)
        html = response.content.decode()
        self.assertIn("NOT-FOUND", html)
        self.assertIn("Access problem", html)
        self.assertIn(response["X-EduSphere-Error-Code"], "NOT-FOUND")
        self.assertTrue(response["X-Request-ID"])

    def test_offline_fallback_page_is_categorised(self):
        response = self.client.get(reverse("offline"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("NET-OFFLINE", html)
        self.assertIn("Network problem", html)
        self.assertIn("What still works offline", html)
        self.assertIn("signed-in pages", html)

    def test_billing_contact_page_stays_available(self):
        response = self.client.get(reverse("billing_contact"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("+263774573765", response.content.decode())

    def test_suspended_institution_gets_org_suspended(self):
        institution = Institution.objects.create(
            name="Suspended School", slug="suspended-school", is_active=False,
            suspended_at=timezone.now(),
        )
        user = User.objects.create_user(
            "suspended-user", password="Str0ngPass!2026", institution=institution
        )
        self.client.force_login(user)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 403)
        html = response.content.decode()
        self.assertIn("ORG-SUSPENDED", html)
        self.assertIn("/billing/contact/", html)

    def test_public_pages_are_marked_offline_cacheable_but_private_ones_are_not(self):
        anon = self.client.get(reverse("login"))
        self.assertEqual(anon.get("X-EduSphere-Offline-Public"), "1")
        user = User.objects.create_user("private-user", password="Str0ngPass!2026")
        self.client.force_login(user)
        private = self.client.get(reverse("dashboard"))
        self.assertNotIn("X-EduSphere-Offline-Public", private)


class PlatformErrorsPageTests(PlatformTestBase):
    def test_errors_page_requires_platform_permission(self):
        self.client.force_login(self.inst_admin)
        self.assertEqual(self.client.get(reverse("platform_admin:errors")).status_code, 404)
        self.login(self.main)
        self.assertEqual(self.client.get(reverse("platform_admin:errors")).status_code, 200)

    def test_filters_and_reference_lookup(self):
        ErrorRecord.objects.create(
            reference="ERR-A1B2C3", category="NETWORK", code="NET-OFFLINE", status=503,
            title="Network problem: your device looks offline", explanation="offline",
            next_steps=["Reconnect", "Try again"],
        )
        ErrorRecord.objects.create(
            reference="ERR-D4E5F6", category="EXTERNAL", code="EXT-EMAIL", status=502,
            title="External service problem", explanation="email failed",
            next_steps=["Retry", "Contact support"],
        )
        self.login(self.main)
        url = reverse("platform_admin:errors")
        response = self.client.get(url, {"category": "NETWORK"})
        self.assertContains(response, "ERR-A1B2C3")
        self.assertNotContains(response, "ERR-D4E5F6")
        response = self.client.get(url, {"code": "EXT-EMAIL"})
        self.assertContains(response, "ERR-D4E5F6")
        response = self.client.get(url, {"reference": "err-a1b2c3"})
        self.assertContains(response, "NETWORK / NET-OFFLINE")
        self.assertContains(response, "Look up a reference")
        self.assertContains(response, "Email health")
        self.assertContains(response, "Jobs")
        self.assertContains(response, "Audit")
        self.assertContains(response, "ERR-A1B2C3")  # trend counts and table

    def test_technical_record_fields_only_for_permitted_staff(self):
        ErrorRecord.objects.create(
            reference="ERR-111111", category="SERVER", code="SRV-500", status=500,
            exception_class="runtime.RuntimeError", view_name="broken.view",
            service="", title="Server problem", explanation="fault", next_steps=[],
        )
        self.assertTrue(can_view_error_details(self.support))
        self.assertFalse(can_view_error_details(self.billing))
        self.login(self.support)
        response = self.client.get(reverse("platform_admin:errors"), {"reference": "ERR-111111"})
        self.assertContains(response, "runtime.RuntimeError")


class BrowserScriptTests(TestCase):
    def test_static_error_script_is_csp_safe_and_covers_network_cases(self):
        js = (BASE_DIR / "static" / "js" / "error-handling.js").read_text(encoding="utf-8")
        self.assertIn("addEventListener(\"online\"", js)
        self.assertIn("addEventListener(\"offline\"", js)
        self.assertIn("You are offline. Your changes will not be saved until you are back online", js)
        self.assertIn("This is taking longer than usual", js)
        self.assertIn("NET-TIMEOUT", js)
        self.assertIn("idempotency_key", js)
        self.assertNotIn("<script", js)
        self.assertNotIn("innerHTML", js)
        self.assertNotIn("hunter2", js)

    def test_templates_load_the_catalogue_as_an_external_script(self):
        base = (BASE_DIR / "templates" / "base.html").read_text(encoding="utf-8")
        public = (BASE_DIR / "templates" / "public_base.html").read_text(encoding="utf-8")
        for template in (base, public):
            self.assertIn("error-handling.js", template)
            self.assertIn("error_catalogue_js", template)
        response = self.client.get(reverse("error_catalogue_js"))
        self.assertEqual(response["Content-Type"], "application/javascript")
        self.assertIn("NET-OFFLINE", response.content.decode())
        self.assertIn("EXT-FISCAL", response.content.decode())

    def test_service_worker_never_caches_authenticated_html(self):
        worker = (BASE_DIR / "static" / "js" / "sw.js").read_text(encoding="utf-8")
        self.assertIn("X-EduSphere-Offline-Public", worker)
        self.assertIn("Set-Cookie", worker)
        self.assertIn("caches.match(\"/offline/\")", worker)
        self.assertIn("request.method !== \"GET\"", worker)


class IdempotencyTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_key_is_stable_and_result_is_reused(self):
        request = RequestFactory().post("/pay/", {"idempotency_key": "same-key"})
        request.user = AnonymousUser()
        token, previous = begin(request, "payment:1")
        self.assertIsNone(previous)
        finish(token, {"redirect_url": "https://example.test/pay"}, ttl=60)
        token2, previous2 = begin(request, "payment:1")
        self.assertEqual(token, token2)
        self.assertEqual(previous2["redirect_url"], "https://example.test/pay")

    @patch("finance.gateways.start_online_payment")
    def test_retried_payment_does_not_start_twice(self, start_payment):
        from finance.factories import InvoiceFactory

        start_payment.return_value = (None, "https://example.test/paynow/redirect")
        user = User.objects.create_superuser(
            "paying-admin", "paying@example.test", "Str0ngPass!2026"
        )
        invoice = InvoiceFactory()
        self.client.force_login(user)
        url = reverse("pay_online", args=[invoice.pk])
        data = {"amount": "25.00", "idempotency_key": new_key()}
        first = self.client.post(url, data)
        second = self.client.post(url, data)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(first["Location"], second["Location"])
        self.assertEqual(start_payment.call_count, 1)

    @patch("accounts.views.register_student")
    def test_retried_registration_does_not_create_twice(self, register):
        student = SimpleNamespace(
            name="Test Student", user=SimpleNamespace(username="newstudent")
        )
        register.return_value = {
            "outcome": "activated",
            "verification_sent": False,
            "student": student,
            "user": student.user,
        }
        institution = Institution.objects.create(
            name="Open College", slug="open-college", public_registration=True
        )
        url = reverse("register_student")
        data = {
            "institution": institution.pk,
            "idempotency_key": new_key(),
            "registration_number": "ST-100",
            "surname": "Moyo",
            "date_of_birth": "2005-01-01",
            "contact": "student@example.test",
            "username": "newstudent",
            "password": "Str0ngPass!2026",
        }
        first = self.client.post(url, data)
        second = self.client.post(url, data)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(register.call_count, 1)
        self.assertContains(second, "Your account is ready")
