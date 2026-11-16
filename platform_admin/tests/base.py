"""Shared fixtures for the platform administration tests.

The tests deliberately build their own institutions and platform staff rows
instead of relying on the migration that seeds the default institution. Every
test therefore proves the same rules from a clean starting point, including the
rule that platform rights come only from an explicit :class:`PlatformStaff`
row.
"""

from __future__ import annotations

import shutil
import tempfile
from datetime import timedelta
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from core.models import Institution
from platform_admin.models import (
    AccessRequest,
    DataAccessGrant,
    PlatformStaff,
)
from platform_admin.services import ensure_default_plans, ensure_platform_roles

User = get_user_model()

STRONG_PASSWORD = "Str0ngPass!2026"
TOTP_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


class MediaMixin:
    """Point file storage at a throwaway directory for one test class."""

    @classmethod
    def setUpClass(cls):
        cls._media_root = tempfile.mkdtemp(prefix="edusphere-platform-media-")
        cls._media_override = override_settings(MEDIA_ROOT=cls._media_root)
        cls._media_override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._media_override.disable()
        shutil.rmtree(cls._media_root, ignore_errors=True)


class PlatformTestBase(TestCase):
    """Institutions, platform roles and one account per platform role."""

    @classmethod
    def setUpTestData(cls):
        cls.inst = Institution.objects.create(
            name="Alpha College", slug="alpha-platform", type="college"
        )
        cls.other = Institution.objects.create(
            name="Beta College", slug="beta-platform", type="college"
        )
        cls.roles = ensure_platform_roles()
        ensure_default_plans()
        from platform_admin.models import Plan

        cls.plans = {plan.code: plan for plan in Plan.objects.all()}

        cls.main, cls.main_staff = cls.make_platform_account(
            "platformmain", "main_admin", is_main_admin=True
        )
        cls.support, cls.support_staff = cls.make_platform_account(
            "platformsupport", "support"
        )
        cls.billing, cls.billing_staff = cls.make_platform_account(
            "platformbilling", "billing"
        )
        cls.compliance, cls.compliance_staff = cls.make_platform_account(
            "platformcompliance", "compliance"
        )
        # A second main administrator for emergency second approvals.
        cls.second, cls.second_staff = cls.make_platform_account(
            "platformsecond", "main_admin", is_main_admin=True
        )

        cls.inst_admin = User.objects.create_user(
            "alphaadmin",
            email="alphaadmin@example.com",
            password=STRONG_PASSWORD,
            role=User.Role.ADMIN,
            institution=cls.inst,
        )
        cls.other_admin = User.objects.create_user(
            "betaadmin",
            email="betaadmin@example.com",
            password=STRONG_PASSWORD,
            role=User.Role.ADMIN,
            institution=cls.other,
        )
        cls.teacher = User.objects.create_user(
            "alphateach",
            email="alphateach@example.com",
            password=STRONG_PASSWORD,
            role=User.Role.LECTURER,
            institution=cls.inst,
        )

    @classmethod
    def make_platform_account(
        cls, username, role_code, *, is_main_admin=False, two_factor=True
    ):
        user = User.objects.create_user(
            username,
            email=f"{username}@edusphere.example",
            password=STRONG_PASSWORD,
            role=User.Role.STAFF,
            institution=cls.inst,
            is_staff=False,
        )
        staff = PlatformStaff.objects.create(
            user=user,
            role=cls.roles[role_code],
            is_main_admin=is_main_admin,
            is_active=True,
            two_factor_enabled=two_factor,
            two_factor_secret=TOTP_SECRET if two_factor else "",
            last_reauth_at=timezone.now() if two_factor else None,
        )
        return user, staff

    def setUp(self):
        from django.core.cache import cache

        cache.clear()

    # ---------- convenience ----------

    def login(self, user):
        self.client.force_login(user)
        if hasattr(user, "platform_staff"):
            user.platform_staff.last_reauth_at = timezone.now()
            user.platform_staff.save(update_fields=["last_reauth_at"])
        self.forget_platform_cache(user)
        return user

    @staticmethod
    def forget_platform_cache(user):
        user.__dict__.pop("_platform_staff_cache", None)

    def make_request(
        self,
        *,
        actor=None,
        institution=None,
        categories=("personal_data",),
        mode=AccessRequest.Mode.READ,
        hours=24,
        include_export=False,
        reason="Investigating a support ticket raised by the school.",
        status=AccessRequest.Status.PENDING,
        create_grant=False,
        grant_hours=None,
    ):
        actor = actor or self.main
        institution = institution or self.inst
        access_request = AccessRequest.objects.create(
            institution=institution,
            requested_by=actor,
            categories=list(categories),
            mode=mode,
            include_export=include_export,
            reason=reason,
            duration_hours=hours,
            status=status,
            expires_at=timezone.now() + timedelta(days=7),
        )
        grant = None
        if create_grant:
            grant = DataAccessGrant.objects.create(
                access_request=access_request,
                institution=institution,
                platform_admin=actor,
                categories=list(categories),
                mode=mode,
                include_export=include_export,
                starts_at=timezone.now() - timedelta(minutes=1),
                expires_at=timezone.now() + timedelta(hours=grant_hours or hours),
            )
        return access_request, grant

    @staticmethod
    def png_upload(name="certificate.png", size=(600, 600)):
        buffer = BytesIO()
        Image.new("RGB", size, "white").save(buffer, format="PNG")
        return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")

    @staticmethod
    def application_payload(**overrides):
        data = {
            "institution_name": "Newtown Secondary School",
            "institution_type": Institution.Type.SECONDARY,
            "country": "Zimbabwe",
            "town": "Harare",
            "registration_number": "REG-12345",
            "expected_students": 800,
            "expected_staff": 45,
            "contact_person": "Tariro Moyo",
            "position": "Head teacher",
            "phone": "+263771234567",
            "email": "head@newtown.example",
            "website": "https://newtown.example",
            "modules": ["core_academics", "finance"],
            "agreed_to_terms": "on",
            "website_url": "",
        }
        data.update(overrides)
        return data
