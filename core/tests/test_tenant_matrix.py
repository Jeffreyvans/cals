"""Systematic cross-tenant ID variation across URL and API detail routes.

The older isolation tests covered a handful of pages. This matrix walks every
registered URL pattern that accepts an object id and every API detail route,
substitutes ids that belong to a second institution, and checks that nothing
from that institution leaks into the response.
"""

import re
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import NoReverseMatch, get_resolver, reverse

from academics.models import Course, Programme, Student
from api.models import ApiKey
from core.models import Institution
from core.roles import ensure_roles
from core.tenancy import use_institution
from finance.models import Invoice, Payment

User = get_user_model()
MARKER = "ZXCVSENTINEL"

# Token links are intentionally usable by anybody who holds the token; they are
# not authenticated tenant routes and are tested separately in their own apps.
PUBLIC_TOKEN_ROUTES = {
    "verify_receipt", "verify_certificate", "verify_card", "offer_accept",
    "offer_letter", "track_application", "application_success",
    "apply_status", "apply_reply", "apply_withdraw",
}


def id_bearing_patterns():
    """(name, route) for every URL pattern with an object-id converter."""
    found = []

    def walk(patterns):
        for pattern in patterns:
            route = str(pattern.pattern)
            if hasattr(pattern, "url_patterns"):
                walk(pattern.url_patterns)
            elif pattern.name and re.search(r"<(?:int|uuid|slug|str):", route):
                found.append((pattern.name, route))

    walk(get_resolver().url_patterns)
    return found


class ForeignData:
    """Build a second institution with objects carrying a unique marker."""

    @classmethod
    def build(cls):
        tenant = Institution.objects.create(
            name="Foreign School", slug="foreign-school", type="college")
        with use_institution(tenant):
            ensure_roles(tenant)
            programme = Programme.objects.create(
                name=f"Foreign Programme {MARKER}", code="FP", level="diploma",
                tuition_fee=Decimal("500"))
            course = Course.objects.create(
                code="F101", title=f"Foreign Course {MARKER}",
                programme=programme, credits=3)
            user = User.objects.create_user(
                "foreign-matrix-student", password="pw123456", role="student",
                first_name=MARKER, institution=tenant)
            student = Student.objects.create(
                user=user, programme=programme, student_no="F0001",
                institution=tenant)
            invoice = Invoice.objects.create(
                student=student, description=f"Foreign Invoice {MARKER}",
                amount=Decimal("100"), due_date=date.today(), institution=tenant)
            payment = Payment.objects.create(
                invoice=invoice, amount=Decimal("100"), method="cash",
                payer_name=f"Foreign Payment {MARKER}", institution=tenant)
        return {
            "tenant": tenant,
            "ids": [student.pk, invoice.pk, payment.pk, programme.pk, course.pk],
            "user_ids": [user.pk],
        }


class WebUrlTenantMatrixTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.home = Institution.objects.create(
            name="Home School", slug="home-school", type="college")
        with use_institution(cls.home):
            ensure_roles(cls.home)
            cls.admin = User.objects.create_user(
                "home-matrix-admin", password="pw123456", role="admin",
                is_staff=True, is_superuser=True, institution=cls.home)
        cls.foreign = ForeignData.build()

    def test_every_id_bearing_web_route_refuses_foreign_ids(self):
        self.client.force_login(self.admin)
        checked = 0
        for name, _route in id_bearing_patterns():
            if (name in PUBLIC_TOKEN_ROUTES or name.startswith("platform_admin:")
                    or name.startswith("api_") or name.endswith("-detail")):
                continue
            for foreign_id in self.foreign["ids"] + self.foreign["user_ids"]:
                try:
                    url = reverse(name, args=[foreign_id])
                except NoReverseMatch:
                    continue
                with self.subTest(route=name, foreign_id=foreign_id):
                    response = self.client.get(url)
                    self.assertLess(response.status_code, 500)
                    if 200 <= response.status_code < 300:
                        self.assertNotIn(MARKER, response.content.decode(errors="replace"))
                    checked += 1
        self.assertGreater(checked, 100, "the URL matrix stopped covering routes")


class ApiDetailTenantMatrixTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.home = Institution.objects.create(
            name="API Home School", slug="api-home-school", type="college")
        with use_institution(cls.home):
            ensure_roles(cls.home)
            cls.admin = User.objects.create_user(
                "api-matrix-admin", password="pw123456", role="admin",
                institution=cls.home)
            _key, cls.raw_key = ApiKey.issue(cls.home, cls.admin, "Matrix key")
        cls.foreign = ForeignData.build()

    def test_every_api_detail_route_refuses_foreign_ids(self):
        detail_names = [
            "student-detail", "staff-detail", "academic-year-detail",
            "term-detail", "programme-detail", "course-detail",
            "subject-detail", "class-detail", "enrolment-detail",
            "attendance-detail", "invoice-detail", "payment-detail",
            "application-detail",
        ]
        checked = 0
        headers = {"HTTP_AUTHORIZATION": f"Api-Key {self.raw_key}"}
        for name in detail_names:
            for foreign_id in self.foreign["ids"] + self.foreign["user_ids"]:
                with self.subTest(route=name, foreign_id=foreign_id):
                    checked += 1
                    response = self.client.get(reverse(name, args=[foreign_id]), **headers)
                    # A foreign id can numerically collide with a valid local
                    # id of a different model (e.g. a Student pk matching a
                    # User pk). What matters is that no foreign data leaks.
                    self.assertLess(response.status_code, 500)
                    body = response.content.decode(errors="replace")
                    self.assertNotIn(MARKER, body)
                    self.assertNotIn("Foreign School", body)
                    self.assertNotIn("foreign-school", body)
        self.assertEqual(checked, len(detail_names) * 6)
