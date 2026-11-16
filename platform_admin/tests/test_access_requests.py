"""Consent-based access: requests, decisions, grants, revocation and emergencies.

The institution owns its data. These tests prove that platform staff cannot see
anything protected without a recorded, time-limited and revocable decision, that
the institution can narrow or refuse a request, and that the most sensitive
categories need the data protection officer or principal.
"""

from __future__ import annotations

from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from core.models import Notification
from platform_admin.models import (
    AccessRequest,
    DataAccessGrant,
    PlatformAuditLog,
    PlatformRole,
)

from .base import PlatformTestBase


class AccessRequestSubmissionTests(PlatformTestBase):
    def test_a_reason_shorter_than_twenty_characters_is_refused(self):
        with self.assertRaises(ValidationError):
            self.submit(reason="Too short")

    def test_a_request_is_stored_with_the_requested_scope(self):
        access_request = self.submit(
            categories=["personal_data", "finance"],
            mode=AccessRequest.Mode.READ,
            hours=12,
            include_export=True,
            reason="Investigating the fee import problem in ticket 4412.",
            reference="TICKET-4412",
        )
        self.assertEqual(access_request.status, AccessRequest.Status.PENDING)
        self.assertEqual(access_request.categories, ["personal_data", "finance"])
        self.assertEqual(access_request.duration_hours, 12)
        self.assertTrue(access_request.include_export)
        self.assertEqual(access_request.reference, "TICKET-4412")
        self.assertIsNone(access_request.decided_by)
        self.assertTrue(
            PlatformAuditLog.objects.filter(action="access.requested").exists()
        )

    def test_the_duration_is_capped_by_the_institution_setting_and_the_hard_limit(self):
        self.inst.access_grant_max_hours = 6
        self.inst.save(update_fields=["access_grant_max_hours"])

        access_request = self.submit(hours=48)
        self.assertEqual(access_request.duration_hours, 6)

        self.inst.access_grant_max_hours = 168
        self.inst.save(update_fields=["access_grant_max_hours"])
        access_request = self.submit(hours=1000)
        self.assertEqual(access_request.duration_hours, 168)

    def test_two_factor_and_a_recent_password_are_needed_to_ask(self):
        no_two_factor, _staff = self.make_platform_account(
            "platformnofactor", "support", two_factor=False
        )
        with self.assertRaisesMessage(ValidationError, "two-factor"):
            self.submit(actor=no_two_factor)

        stale, staff = self.make_platform_account("platformstale", "support")
        staff.last_reauth_at = timezone.now() - timedelta(minutes=30)
        staff.save(update_fields=["last_reauth_at"])
        self.forget_platform_cache(stale)
        with self.assertRaisesMessage(ValidationError, "password again"):
            self.submit(actor=stale)

    def test_only_staff_with_the_request_permission_may_ask(self):
        with self.assertRaises(PermissionDenied):
            self.submit(actor=self.teacher)

    def test_the_request_reaches_institution_administrators_by_email_and_in_app(self):
        access_request = self.submit()

        self.assertTrue(
            Notification.objects.filter(
                user=self.inst_admin,
                category="access",
                title__icontains="asked for access",
            ).exists()
        )
        self.assertFalse(
            Notification.objects.filter(
                user=self.other_admin,
                category="access",
            ).exists()
        )
        # An email went to the administrator of the institution asked.
        from core.models import OutboundMessage

        self.assertTrue(
            OutboundMessage.objects.filter(
                recipient="alphaadmin@example.com",
                subject__icontains="asking for access",
            ).exists()
        )
        self.assertEqual(access_request.institution, self.inst)

    def test_a_request_for_another_institution_does_not_notify_the_first_school(self):
        self.submit(institution=self.other)
        self.assertFalse(
            Notification.objects.filter(user=self.inst_admin, category="access").exists()
        )
        self.assertTrue(
            Notification.objects.filter(user=self.other_admin, category="access").exists()
        )

    def submit(self, actor=None, institution=None, **kwargs):
        from platform_admin.services import submit_access_request

        actor = actor or self.main
        institution = institution or self.inst
        options = {
            "categories": ["personal_data"],
            "mode": AccessRequest.Mode.READ,
            "reason": "Investigating a support ticket raised by the school.",
            "hours": 24,
        }
        options.update(kwargs)
        return submit_access_request(actor, institution, **options)


class AccessDecisionTests(PlatformTestBase):
    def setUp(self):
        super().setUp()
        self.login(self.inst_admin)

    def test_the_institution_approves_and_a_scoped_grant_appears(self):
        access_request, _ = self.make_request(categories=("personal_data",), hours=24)
        response = self.client.post(
            reverse("platform_admin:institution_access_decide", args=[access_request.pk]),
            {
                "decision": "approve",
                "categories": ["personal_data"],
                "mode": AccessRequest.Mode.READ,
                "hours": 24,
            },
        )
        self.assertEqual(response.status_code, 302)
        access_request.refresh_from_db()
        self.assertEqual(access_request.status, AccessRequest.Status.APPROVED)
        self.assertEqual(access_request.decided_by, self.inst_admin)
        grant = access_request.grant
        self.assertEqual(grant.categories, ["personal_data"])
        self.assertEqual(grant.mode, AccessRequest.Mode.READ)
        self.assertFalse(grant.include_export)
        self.assertTrue(grant.is_active)
        self.assertEqual(grant.platform_admin, self.main)
        self.assertTrue(
            PlatformAuditLog.objects.filter(action="access.approved", grant=grant).exists()
        )

    def test_approval_can_narrow_categories_and_time_but_never_widen_them(self):
        access_request, _ = self.make_request(
            categories=("personal_data", "finance"), hours=24
        )
        from platform_admin.services import decide_access_request

        with self.assertRaises(ValidationError):
            decide_access_request(
                access_request,
                self.inst_admin,
                True,
                categories=["personal_data", "health"],
            )

        decide_access_request(
            access_request,
            self.inst_admin,
            True,
            categories=["personal_data"],
            hours=4,
        )
        grant = access_request.grant
        self.assertEqual(grant.categories, ["personal_data"])
        remaining = grant.expires_at - timezone.now()
        self.assertLessEqual(remaining, timedelta(hours=4, seconds=5))
        self.assertGreater(remaining, timedelta(hours=3))

    def test_a_denial_needs_a_reason_and_creates_no_grant(self):
        access_request, _ = self.make_request()
        from platform_admin.services import decide_access_request

        with self.assertRaises(ValidationError):
            decide_access_request(access_request, self.inst_admin, False, reason="No")

        decide_access_request(
            access_request,
            self.inst_admin,
            False,
            reason="The ticket does not need personal data.",
        )
        access_request.refresh_from_db()
        self.assertEqual(access_request.status, AccessRequest.Status.DENIED)
        self.assertEqual(
            access_request.decision_reason, "The ticket does not need personal data."
        )
        self.assertFalse(DataAccessGrant.objects.filter(access_request=access_request).exists())
        self.assertTrue(PlatformAuditLog.objects.filter(action="access.denied").exists())

    def test_health_and_counselling_need_the_dpo_or_principal(self):
        access_request, _ = self.make_request(categories=("health",))
        from platform_admin.services import decide_access_request

        with self.assertRaisesMessage(ValidationError, "data protection officer"):
            decide_access_request(access_request, self.inst_admin, True)

        self.inst.data_protection_officer = self.inst_admin
        self.inst.save(update_fields=["data_protection_officer"])
        decide_access_request(access_request, self.inst_admin, True)
        access_request.refresh_from_db()
        self.assertEqual(access_request.status, AccessRequest.Status.APPROVED)
        self.assertEqual(access_request.grant.categories, ["health"])

    def test_a_principal_may_approve_health_but_an_outsider_may_not(self):
        access_request, _ = self.make_request(categories=("counselling",))
        from platform_admin.services import decide_access_request

        with self.assertRaises(PermissionDenied):
            decide_access_request(access_request, self.other_admin, True)

        self.inst.principal = self.inst_admin
        self.inst.save(update_fields=["principal"])
        decide_access_request(access_request, self.inst_admin, True)
        self.assertEqual(access_request.grant.categories, ["counselling"])

    def test_a_platform_admin_cannot_approve_their_own_request(self):
        access_request, _ = self.make_request()
        from platform_admin.services import decide_access_request

        with self.assertRaisesMessage(ValidationError, "own access request"):
            decide_access_request(access_request, self.main, True)

    def test_a_teacher_and_a_platform_admin_cannot_decide(self):
        access_request, _ = self.make_request()
        from platform_admin.services import decide_access_request

        for actor in (self.teacher, self.support):
            with self.assertRaises(PermissionDenied):
                decide_access_request(access_request, actor, True)

    def test_a_request_for_another_institution_cannot_be_opened_or_decided(self):
        access_request, _ = self.make_request(institution=self.other)
        # The first school's administrator cannot even open the page.
        response = self.client.get(
            reverse(
                "platform_admin:institution_access_request_detail",
                args=[access_request.pk],
            )
        )
        self.assertEqual(response.status_code, 404)
        response = self.client.post(
            reverse("platform_admin:institution_access_decide", args=[access_request.pk]),
            {"decision": "approve", "categories": ["personal_data"]},
        )
        self.assertEqual(response.status_code, 404)
        access_request.refresh_from_db()
        self.assertEqual(access_request.status, AccessRequest.Status.PENDING)

    def test_the_consent_screen_lists_requests_and_names_the_requester(self):
        access_request, _ = self.make_request()
        response = self.client.get(reverse("platform_admin:institution_access_requests"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "platformmain")
        response = self.client.get(
            reverse(
                "platform_admin:institution_access_request_detail",
                args=[access_request.pk],
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Investigating a support ticket")


class AccessExpiryAndRevocationTests(PlatformTestBase):
    def test_a_request_waits_and_then_expires_it_is_never_auto_approved(self):
        access_request, _ = self.make_request()
        self.assertEqual(access_request.status, AccessRequest.Status.PENDING)

        from platform_admin.services import expire_stale_access

        later = timezone.now() + timedelta(days=8)
        expire_stale_access(now=later)
        access_request.refresh_from_db()
        self.assertEqual(access_request.status, AccessRequest.Status.EXPIRED)
        self.assertFalse(DataAccessGrant.objects.filter(access_request=access_request).exists())
        self.assertTrue(PlatformAuditLog.objects.filter(action="access.expired").exists())

    def test_an_approved_grant_expires_and_is_swept(self):
        access_request, grant = self.make_request(
            status=AccessRequest.Status.APPROVED, create_grant=True, grant_hours=1
        )
        from platform_admin.services import expire_stale_access

        expire_stale_access(now=timezone.now() + timedelta(hours=2))
        access_request.refresh_from_db()
        grant.refresh_from_db()
        self.assertEqual(access_request.status, AccessRequest.Status.EXPIRED)
        self.assertFalse(grant.is_active)

    def test_the_institution_can_revoke_a_grant_at_once(self):
        access_request, grant = self.make_request(
            status=AccessRequest.Status.APPROVED, create_grant=True
        )
        self.login(self.inst_admin)
        response = self.client.post(
            reverse("platform_admin:institution_grant_revoke", args=[grant.pk]),
            {"reason": "The ticket is closed."},
        )
        self.assertEqual(response.status_code, 302)
        grant.refresh_from_db()
        access_request.refresh_from_db()
        self.assertIsNotNone(grant.revoked_at)
        self.assertEqual(grant.revoked_by, self.inst_admin)
        self.assertEqual(grant.revoked_reason, "The ticket is closed.")
        self.assertEqual(access_request.status, AccessRequest.Status.REVOKED)
        self.assertFalse(grant.is_active)
        self.assertTrue(PlatformAuditLog.objects.filter(action="access.revoked").exists())
        self.assertTrue(
            Notification.objects.filter(
                user=self.main, title__icontains="revoked"
            ).exists()
        )

    def test_a_grant_only_works_for_the_institution_that_consented(self):
        _access_request, grant = self.make_request(
            institution=self.inst,
            status=AccessRequest.Status.APPROVED,
            create_grant=True,
        )
        from platform_admin.services import active_grant_for, grant_for_request

        self.forget_platform_cache(self.main)
        self.assertEqual(active_grant_for(self.main, self.inst), grant)
        self.assertIsNone(active_grant_for(self.main, self.other))
        self.assertIsNone(grant_for_request(self.main, self.other))

    def test_a_grant_stops_working_after_fifteen_idle_minutes(self):
        _access_request, grant = self.make_request(
            status=AccessRequest.Status.APPROVED, create_grant=True
        )
        grant.last_seen_at = timezone.now() - timedelta(minutes=16)
        grant.save(update_fields=["last_seen_at"])
        from platform_admin.services import active_grant_for, grant_for_request

        self.forget_platform_cache(self.main)
        self.assertIsNotNone(active_grant_for(self.main, self.inst))
        self.assertIsNone(grant_for_request(self.main, self.inst))


class EmergencyAccessTests(PlatformTestBase):
    def test_emergency_access_needs_a_real_justification(self):
        from platform_admin.services import submit_emergency_access

        with self.assertRaises(ValidationError):
            submit_emergency_access(self.main, self.inst, ["health"], "Outage")

    def test_a_second_platform_admin_approves_a_two_hour_read_only_grant(self):
        from platform_admin.services import approve_emergency, submit_emergency_access

        access_request = submit_emergency_access(
            self.main,
            self.inst,
            ["health", "personal_data"],
            "A security incident is locking staff out of the clinic records.",
        )
        self.assertTrue(access_request.is_emergency)
        self.assertTrue(access_request.flagged_for_review)
        self.assertEqual(access_request.duration_hours, 2)
        self.assertEqual(access_request.status, AccessRequest.Status.PENDING)

        # The requester may not approve it themselves.
        with self.assertRaisesMessage(ValidationError, "second platform administrator"):
            approve_emergency(access_request, self.main)

        approve_emergency(access_request, self.second)
        access_request.refresh_from_db()
        self.assertEqual(access_request.status, AccessRequest.Status.APPROVED)
        self.assertEqual(access_request.second_approver, self.second)
        grant = access_request.grant
        self.assertEqual(grant.mode, AccessRequest.Mode.READ)
        self.assertFalse(grant.include_export)
        remaining = grant.expires_at - timezone.now()
        self.assertLessEqual(remaining, timedelta(hours=2, seconds=5))

        # The institution administrator is told immediately.
        self.assertTrue(
            Notification.objects.filter(
                user=self.inst_admin, title__icontains="Emergency EduSphere access"
            ).exists()
        )
        self.assertTrue(
            PlatformAuditLog.objects.filter(action="access.emergency_approved").exists()
        )

    def test_an_emergency_approver_must_have_two_factor(self):
        from platform_admin.services import approve_emergency, submit_emergency_access

        access_request = submit_emergency_access(
            self.main,
            self.inst,
            ["personal_data"],
            "The payments service is down and invoices cannot be checked.",
        )
        self.second_staff.two_factor_enabled = False
        self.second_staff.save(update_fields=["two_factor_enabled"])
        with self.assertRaisesMessage(ValidationError, "two-factor"):
            approve_emergency(access_request, self.second)

    def test_a_platform_admin_cannot_approve_another_kind_of_request_as_emergency(self):
        access_request, _ = self.make_request()
        from platform_admin.services import approve_emergency

        with self.assertRaisesMessage(ValidationError, "not an emergency"):
            approve_emergency(access_request, self.second)


class UnusualPatternTests(PlatformTestBase):
    def _seed_requests(self, actor, count, status=AccessRequest.Status.PENDING):
        for index in range(count):
            AccessRequest.objects.create(
                institution=self.inst,
                requested_by=actor,
                categories=["personal_data"],
                reason=f"Support investigation number {index} for the school.",
                status=status,
                decided_at=timezone.now() if status != AccessRequest.Status.PENDING else None,
                expires_at=timezone.now() + timedelta(days=7),
            )

    def test_many_requests_in_one_day_alert_the_other_platform_admins(self):
        self._seed_requests(self.support, 6)
        from platform_admin.services import check_unusual_patterns

        reasons = check_unusual_patterns(self.support)
        self.assertTrue(any("6 access requests" in reason for reason in reasons))
        self.assertTrue(
            Notification.objects.filter(
                user=self.main, category="security", title__icontains="Unusual"
            ).exists()
        )
        self.assertTrue(
            PlatformAuditLog.objects.filter(action="platform.pattern_alert").exists()
        )

    def test_repeated_denials_are_alerted(self):
        self._seed_requests(self.support, 3, status=AccessRequest.Status.DENIED)
        from platform_admin.services import check_unusual_patterns

        reasons = check_unusual_patterns(self.support)
        self.assertTrue(any("denied requests" in reason for reason in reasons))

    def test_a_normal_volume_of_requests_does_not_alert(self):
        self._seed_requests(self.support, 2)
        from platform_admin.services import check_unusual_patterns

        self.assertEqual(check_unusual_patterns(self.support), [])


class AccessRequestPageTests(PlatformTestBase):
    def test_a_support_admin_can_ask_through_the_page(self):
        self.login(self.support)
        response = self.client.post(
            reverse("platform_admin:access_request_create"),
            {
                "institution": self.inst.pk,
                "categories": ["personal_data"],
                "mode": AccessRequest.Mode.READ,
                "reason": "Investigating the parent portal login issue in ticket 9001.",
                "duration_hours": 8,
                "reference": "TICKET-9001",
            },
        )
        self.assertEqual(response.status_code, 302)
        access_request = AccessRequest.objects.get()
        self.assertEqual(access_request.requested_by, self.support)
        self.assertEqual(access_request.duration_hours, 8)
        self.assertEqual(access_request.reference, "TICKET-9001")

    def test_the_page_refuses_a_short_reason_without_creating_a_row(self):
        self.login(self.support)
        response = self.client.post(
            reverse("platform_admin:access_request_create"),
            {
                "institution": self.inst.pk,
                "categories": ["personal_data"],
                "mode": AccessRequest.Mode.READ,
                "reason": "Short",
                "duration_hours": 8,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(AccessRequest.objects.exists())

    def test_staff_without_the_access_permission_cannot_open_the_pages(self):
        # Every preset happens to include the request permission, so build a
        # custom role that can view institutions but not ask for data.
        role = PlatformRole.objects.create(
            code="viewer-only",
            name="Viewer only",
            permissions=["platform.view_institutions"],
        )
        user, staff = self.make_platform_account("platformviewer", "support")
        staff.role = role
        staff.save(update_fields=["role"])
        self.forget_platform_cache(user)
        self.login(user)
        self.assertEqual(
            self.client.get(reverse("platform_admin:access_request_create")).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(reverse("platform_admin:grants")).status_code, 403
        )

    def test_the_emergency_page_records_a_flagged_request(self):
        self.login(self.support)
        response = self.client.post(
            reverse("platform_admin:emergency_access_create"),
            {
                "institution": self.inst.pk,
                "categories": ["personal_data"],
                "justification": "A ransomware incident has taken the student portal offline.",
            },
        )
        self.assertEqual(response.status_code, 302)
        access_request = AccessRequest.objects.get()
        self.assertTrue(access_request.is_emergency)
        self.assertTrue(access_request.flagged_for_review)

    def test_a_platform_admin_never_sees_the_institution_consent_screens(self):
        self.login(self.main)
        for name in (
            "institution_access_requests",
            "institution_data_access",
            "institution_consent_log",
        ):
            self.assertEqual(self.client.get(reverse(f"platform_admin:{name}")).status_code, 403)
