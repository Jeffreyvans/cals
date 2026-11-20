"""The tamper-evident log and the middleware that enforces consent.

These tests follow the data path, not the page. They call the same guard the
middleware calls, prove that a grant is scoped to its categories, mode, time and
export flag, and prove that the audit log cannot be edited or deleted.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.http import HttpResponse
from django.test import RequestFactory, override_settings
from django.urls import path, reverse
from django.utils import timezone

from config.urls import urlpatterns as project_urlpatterns
from core.audit import log_action
from platform_admin.access import (
    check_platform_institution_access,
    grant_covers,
    is_export_path,
)
from platform_admin.models import (
    AccessRequest,
    PlatformAuditLog,
)
from platform_admin.services import log_platform_action

from .base import PlatformTestBase


def _dummy_csv(request):
    return HttpResponse("name,balance\nA,10\n", content_type="text/csv; charset=utf-8")


# A tiny URLconf used only by the export-watermark tests. The real routes stay
# available so reverse() and the platform pages keep working.
urlpatterns = [path("health/export.csv", _dummy_csv)] + list(project_urlpatterns)


class AuditLogIntegrityTests(PlatformTestBase):
    def test_the_log_refuses_bulk_edits_and_deletes(self):
        entry = log_platform_action(self.main, "platform.test", record_type="Thing", record_id=1)

        with self.assertRaises(PermissionDenied):
            PlatformAuditLog.objects.update(action="changed")
        with self.assertRaises(PermissionDenied):
            PlatformAuditLog.objects.all().delete()

        entry.action = "changed"
        with self.assertRaises(PermissionDenied):
            entry.save()
        with self.assertRaises(PermissionDenied):
            entry.delete()

        entry.refresh_from_db()
        self.assertEqual(entry.action, "platform.test")

    def test_the_hash_chain_matches_while_nothing_is_touched(self):
        for index in range(3):
            log_platform_action(self.main, f"platform.test.{index}")
        self.assertTrue(PlatformAuditLog.verify_chain())
        entries = list(PlatformAuditLog.objects.order_by("pk"))
        self.assertEqual(entries[0].prev_hash, "GENESIS")
        self.assertEqual(entries[1].prev_hash, entries[0].entry_hash)

    def test_a_row_changed_behind_the_model_breaks_the_chain(self):
        target = log_platform_action(self.main, "platform.test.one")
        log_platform_action(self.main, "platform.test.two")
        self.assertTrue(PlatformAuditLog.verify_chain())

        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {PlatformAuditLog._meta.db_table} SET action = %s WHERE id = %s",
                ["platform.tampered", target.pk],
            )
        self.assertFalse(PlatformAuditLog.verify_chain())

    def test_a_platform_change_is_mirrored_with_before_after_and_the_grant(self):
        _access_request, grant = self.make_request(
            status=AccessRequest.Status.APPROVED, create_grant=True
        )
        self.forget_platform_cache(self.main)
        log_action(
            self.main,
            "student.updated",
            institution=self.inst,
            model_name="Student",
            object_id="42",
            before={"name": "Old Name"},
            after={"name": "New Name"},
        )
        entry = PlatformAuditLog.objects.get(action="institution.change.student.updated")
        self.assertEqual(entry.institution, self.inst)
        self.assertEqual(entry.grant, grant)
        self.assertEqual(entry.record_type, "Student")
        self.assertEqual(entry.record_id, "42")
        self.assertEqual(entry.before, {"name": "Old Name"})
        self.assertEqual(entry.after, {"name": "New Name"})

    def test_an_institution_change_by_a_teacher_is_not_mirrored(self):
        log_action(
            self.teacher,
            "student.updated",
            institution=self.inst,
            model_name="Student",
            object_id="42",
            before={"name": "Old"},
            after={"name": "New"},
        )
        self.assertFalse(
            PlatformAuditLog.objects.filter(
                action="institution.change.student.updated"
            ).exists()
        )


class GuardUnitTests(PlatformTestBase):
    """Call the exact function the middleware calls."""

    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()
        self.forget_platform_cache(self.main)

    def request_for(self, path, method="get"):
        request = getattr(self.factory, method)(path)
        request.user = self.main
        request.institution = self.inst
        return request

    def test_a_personal_data_grant_cannot_open_finance(self):
        self.make_request(
            categories=("personal_data",),
            status=AccessRequest.Status.APPROVED,
            create_grant=True,
        )
        self.forget_platform_cache(self.main)
        with self.assertRaises(PermissionDenied):
            check_platform_institution_access(self.request_for("/finance/invoices/"))

    def test_the_same_grant_opens_the_screen_it_covers(self):
        self.make_request(
            categories=("personal_data",),
            status=AccessRequest.Status.APPROVED,
            create_grant=True,
        )
        self.forget_platform_cache(self.main)
        allowed = check_platform_institution_access(self.request_for("/admissions/"))
        self.assertIsNotNone(allowed)
        self.assertFalse(allowed["export"])

    def test_no_grant_at_all_is_refused_and_logged(self):
        request = self.request_for("/health/")
        with self.assertRaises(PermissionDenied):
            check_platform_institution_access(request)
        self.assertTrue(
            PlatformAuditLog.objects.filter(action="platform.access_denied").exists()
        )

    def test_a_read_only_grant_refuses_a_post(self):
        self.make_request(
            categories=("finance",),
            status=AccessRequest.Status.APPROVED,
            create_grant=True,
        )
        self.forget_platform_cache(self.main)
        with self.assertRaisesMessage(PermissionDenied, "read-only"):
            check_platform_institution_access(
                self.request_for("/finance/invoices/", method="post")
            )

    def test_a_write_grant_needs_both_the_category_and_the_changes_flag(self):
        _request, _grant = self.make_request(
            categories=("finance", "changes"),
            mode=AccessRequest.Mode.READ_WRITE,
            status=AccessRequest.Status.APPROVED,
            create_grant=True,
        )
        self.forget_platform_cache(self.main)
        allowed = check_platform_institution_access(
            self.request_for("/finance/invoices/", method="post")
        )
        self.assertIsNotNone(allowed)

    def test_an_export_needs_the_export_flag_on_the_grant(self):
        self.make_request(
            categories=("health",),
            include_export=False,
            status=AccessRequest.Status.APPROVED,
            create_grant=True,
        )
        self.forget_platform_cache(self.main)
        with self.assertRaisesMessage(PermissionDenied, "does not include export"):
            check_platform_institution_access(
                self.request_for("/health/export.csv")
            )

    def test_an_expired_grant_is_refused(self):
        _request, grant = self.make_request(
            categories=("personal_data",),
            status=AccessRequest.Status.APPROVED,
            create_grant=True,
        )
        grant.expires_at = timezone.now() - timedelta(minutes=1)
        grant.save(update_fields=["expires_at"])
        self.forget_platform_cache(self.main)
        with self.assertRaises(PermissionDenied):
            check_platform_institution_access(self.request_for("/admissions/"))

    def test_a_grant_left_idle_for_fifteen_minutes_is_refused(self):
        _request, grant = self.make_request(
            categories=("personal_data",),
            status=AccessRequest.Status.APPROVED,
            create_grant=True,
        )
        grant.last_seen_at = timezone.now() - timedelta(minutes=16)
        grant.save(update_fields=["last_seen_at"])
        self.forget_platform_cache(self.main)
        with self.assertRaises(PermissionDenied):
            check_platform_institution_access(self.request_for("/admissions/"))

    def test_two_factor_must_be_on_before_a_grant_can_be_used(self):
        self.make_request(
            categories=("personal_data",),
            status=AccessRequest.Status.APPROVED,
            create_grant=True,
        )
        self.main_staff.two_factor_enabled = False
        self.main_staff.save(update_fields=["two_factor_enabled"])
        self.forget_platform_cache(self.main)
        with self.assertRaisesMessage(PermissionDenied, "two-factor"):
            check_platform_institution_access(self.request_for("/admissions/"))

    def test_a_recent_password_check_is_required_before_a_grant_can_be_used(self):
        self.make_request(
            categories=("personal_data",),
            status=AccessRequest.Status.APPROVED,
            create_grant=True,
        )
        self.main_staff.last_reauth_at = timezone.now() - timedelta(minutes=30)
        self.main_staff.save(update_fields=["last_reauth_at"])
        self.forget_platform_cache(self.main)
        with self.assertRaisesMessage(PermissionDenied, "password again"):
            check_platform_institution_access(self.request_for("/admissions/"))

    def test_platform_pages_are_never_treated_as_protected_data(self):
        request = self.request_for("/platform/institutions/")
        self.assertIsNone(check_platform_institution_access(request))

    def test_export_paths_are_recognised(self):
        self.assertTrue(is_export_path("/finance/invoices/export/"))
        self.assertTrue(is_export_path("/reports/term.csv"))
        self.assertFalse(is_export_path("/finance/"))

    def test_grant_covers_needs_the_mode_and_the_changes_flag_for_writes(self):
        self.make_request(
            categories=("finance",),
            mode=AccessRequest.Mode.READ_WRITE,
            status=AccessRequest.Status.APPROVED,
            create_grant=True,
        )
        self.forget_platform_cache(self.main)
        from platform_admin.services import active_grant_for

        grant = active_grant_for(self.main, self.inst)
        self.assertTrue(grant_covers(grant, {"finance"}))
        self.assertFalse(grant_covers(grant, {"finance"}, write=True))
        self.assertFalse(grant_covers(grant, {"health"}))


class MiddlewareEndToEndTests(PlatformTestBase):
    def test_a_platform_admin_without_consent_is_refused_at_the_school_door(self):
        self.login(self.main)
        for target in ("/finance/", "/health/", "/academics/"):
            response = self.client.get(target)
            self.assertEqual(response.status_code, 403, target)

    def test_a_granted_staff_member_using_an_export_is_given_a_watermark(self):
        _request, grant = self.make_request(
            categories=("health",),
            include_export=True,
            status=AccessRequest.Status.APPROVED,
            create_grant=True,
        )
        self.login(self.main)
        with override_settings(ROOT_URLCONF=__name__):
            response = self.client.get("/health/export.csv")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-EduSphere-Grant-Id"], str(grant.pk))
        self.assertTrue(
            response.content.startswith(
                f"# Platform grant {grant.pk}: Access granted until".encode()
            )
        )
        self.assertEqual(
            PlatformAuditLog.objects.filter(action="platform.export", grant=grant).count(),
            1,
        )

    def test_a_grant_without_export_permission_is_refused_before_the_view_runs(self):
        self.make_request(
            categories=("health",),
            include_export=False,
            status=AccessRequest.Status.APPROVED,
            create_grant=True,
        )
        self.login(self.main)
        with override_settings(ROOT_URLCONF=__name__):
            response = self.client.get("/health/export.csv")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            PlatformAuditLog.objects.filter(action="platform.export").exists()
        )

    def test_a_non_csv_response_is_marked_but_not_rewritten(self):
        _request, grant = self.make_request(
            categories=("health",),
            include_export=True,
            status=AccessRequest.Status.APPROVED,
            create_grant=True,
        )
        self.login(self.main)
        with override_settings(ROOT_URLCONF=__name__):
            # /health/export.csv is a csv here, so a normal page is used to prove
            # that only CSV bodies are stamped by the middleware.
            response = self.client.get(reverse("platform_admin:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("X-EduSphere-Grant-Id", response)
        self.assertNotIn(b"# Platform grant", response.content)


class WhoAccessedOurDataTests(PlatformTestBase):
    def test_the_institution_sees_and_downloads_every_platform_view(self):
        log_platform_action(
            self.main,
            "platform.view",
            institution=self.inst,
            record_type="path",
            record_id="/academics/students/",
            detail={"label": "Student records"},
        )
        self.login(self.inst_admin)

        response = self.client.get(reverse("platform_admin:institution_data_access"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Who accessed our data")
        self.assertContains(response, "platform.view")
        self.assertContains(response, "platformmain")

        response = self.client.get(
            reverse("platform_admin:institution_data_access_export")
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertIn(b"platform.view", response.content)

    def test_the_institution_never_sees_another_schools_access_log(self):
        log_platform_action(
            self.main,
            "platform.view",
            institution=self.other,
            record_type="path",
            record_id="/academics/students/",
        )
        self.login(self.inst_admin)
        response = self.client.get(reverse("platform_admin:institution_data_access"))
        self.assertNotContains(response, "/academics/students/")
        response = self.client.get(
            reverse("platform_admin:institution_data_access_export")
        )
        self.assertNotIn(b"platform.view", response.content)

    def test_an_active_grant_is_listed_and_can_be_revoked_from_that_page(self):
        _request, grant = self.make_request(
            status=AccessRequest.Status.APPROVED, create_grant=True
        )
        self.login(self.inst_admin)
        response = self.client.get(reverse("platform_admin:institution_data_access"))
        self.assertContains(response, "platformmain")
        self.assertContains(response, "Student and staff personal data")
        response = self.client.post(
            reverse("platform_admin:institution_grant_revoke", args=[grant.pk]),
            {"reason": "No longer needed."},
        )
        self.assertEqual(response.status_code, 302)
        grant.refresh_from_db()
        self.assertIsNotNone(grant.revoked_at)

    def test_the_active_grant_banner_names_the_time_categories_and_mode(self):
        _request, grant = self.make_request(
            categories=("finance",),
            mode=AccessRequest.Mode.READ,
            status=AccessRequest.Status.APPROVED,
            create_grant=True,
        )
        self.assertIn("Access granted until", grant.banner)
        self.assertIn("Fees, payments and invoices", grant.banner)
        self.assertIn("read-only", grant.banner)

        self.login(self.main)
        response = self.client.get(reverse("platform_admin:dashboard"))
        self.assertContains(response, "Access granted until")


class AuditPageTests(PlatformTestBase):
    def test_compliance_sees_the_log_and_can_download_it(self):
        log_platform_action(self.main, "platform.test", detail={"answer": 42})
        self.login(self.compliance)
        response = self.client.get(reverse("platform_admin:audit_log"))
        self.assertContains(response, "hash chain is intact")
        self.assertContains(response, "platform.test")

        response = self.client.get(reverse("platform_admin:audit_export"))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"platform.test", response.content)

    def test_the_export_contains_the_hash_for_offline_verification(self):
        entry = log_platform_action(self.main, "platform.test")
        self.login(self.compliance)
        response = self.client.get(reverse("platform_admin:audit_export"))
        self.assertIn(entry.entry_hash.encode(), response.content)

    def test_the_log_is_not_editable_from_the_admin_site(self):
        log_platform_action(self.main, "platform.test")
        self.assertNotIn(PlatformAuditLog, admin.site._registry)
        self.assertFalse(
            admin.site.is_registered(PlatformAuditLog),
            "The platform audit log must not be registered for editing.",
        )
