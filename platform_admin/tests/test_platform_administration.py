"""Platform administration: institutions, money, staff, operations and settings.

The platform is a separate world from an institution. These tests prove that a
platform role only reaches the tools it was given, that the main administrator
can run the whole platform without ever touching student records, and that
every institution change is written to the tamper-evident platform log.
"""

from __future__ import annotations

from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from core.models import Institution, InstitutionModule, Notification
from platform_admin.models import (
    Plan,
    PlatformAnnouncement,
    PlatformAuditLog,
    PlatformInvoice,
    PlatformPayment,
    PlatformRequest,
    PlatformRole,
    PlatformSetting,
    PlatformStaff,
    Subscription,
)

from .base import STRONG_PASSWORD, PlatformTestBase


class InstitutionAdministrationTests(PlatformTestBase):
    def test_support_can_see_institutions_but_cannot_edit_or_suspend_them(self):
        self.login(self.support)

        response = self.client.get(
            reverse("platform_admin:institution_detail", args=[self.inst.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alpha College")

        response = self.client.post(
            reverse("platform_admin:institution_detail", args=[self.inst.pk]),
            {"action": "suspend", "reason": "Unpaid invoice."},
        )
        self.assertEqual(response.status_code, 403)
        self.inst.refresh_from_db()
        self.assertTrue(self.inst.is_active)
        self.assertIsNone(self.inst.suspended_at)

        response = self.client.post(
            reverse("platform_admin:institution_detail", args=[self.inst.pk]),
            {"action": "edit", "name": "Renamed College", "type": "college"},
        )
        self.assertEqual(response.status_code, 403)
        self.inst.refresh_from_db()
        self.assertEqual(self.inst.name, "Alpha College")

    def test_main_admin_suspends_reinstates_archives_restores_and_deletes(self):
        self.login(self.main)
        url = reverse("platform_admin:institution_detail", args=[self.inst.pk])

        response = self.client.post(
            url, {"action": "suspend", "reason": "Safeguarding investigation."}
        )
        self.assertEqual(response.status_code, 302)
        self.inst.refresh_from_db()
        self.assertFalse(self.inst.is_active)
        self.assertIsNotNone(self.inst.suspended_at)
        self.assertEqual(self.inst.suspended_reason, "Safeguarding investigation.")

        self.client.post(url, {"action": "reinstate"})
        self.inst.refresh_from_db()
        self.assertTrue(self.inst.is_active)
        self.assertIsNone(self.inst.suspended_at)

        self.client.post(url, {"action": "archive", "reason": "Closed in 2025."})
        self.inst.refresh_from_db()
        self.assertFalse(self.inst.is_active)
        self.assertIsNotNone(self.inst.archived_at)

        self.client.post(url, {"action": "restore"})
        self.inst.refresh_from_db()
        self.assertIsNone(self.inst.archived_at)

        self.client.post(url, {"action": "delete"})
        self.inst.refresh_from_db()
        self.assertIsNotNone(self.inst.deleted_at)
        self.assertFalse(self.inst.is_active)

        # A deleted institution cannot be opened again, but its row is kept so
        # the audit and consent records survive.
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)
        response = self.client.get(reverse("platform_admin:institutions"))
        listed = list(response.context["institutions"])
        self.assertNotIn(self.inst, listed)

        for action in ("suspend", "reinstate", "archive", "restore", "delete"):
            self.assertTrue(
                PlatformAuditLog.objects.filter(
                    action=f"institution.{action}",
                    institution=self.inst,
                ).exists(),
                f"missing audit row for institution.{action}",
            )
        self.assertTrue(PlatformAuditLog.verify_chain())

    def test_the_institution_list_filters_by_status_and_search(self):
        Institution.objects.create(
            name="Suspended School", slug="suspended-school", is_active=False,
            suspended_at=timezone.now(),
        )
        Institution.objects.create(
            name="Archived Academy", slug="archived-academy", is_active=False,
            archived_at=timezone.now(),
        )
        self.login(self.main)

        response = self.client.get(reverse("platform_admin:institutions"), {"status": "suspended"})
        self.assertContains(response, "Suspended School")
        self.assertNotContains(response, "Archived Academy")

        response = self.client.get(reverse("platform_admin:institutions"), {"status": "archived"})
        self.assertContains(response, "Archived Academy")
        self.assertNotContains(response, "Suspended School")

        response = self.client.get(reverse("platform_admin:institutions"), {"q": "alpha"})
        self.assertContains(response, "Alpha College")
        self.assertNotContains(response, "Suspended School")

    def test_editing_an_institution_records_before_and_after(self):
        self.login(self.main)
        response = self.client.post(
            reverse("platform_admin:institution_detail", args=[self.inst.pk]),
            {
                "action": "edit",
                "name": "Alpha College Renamed",
                "type": "secondary",
                "address": "1 Main Street",
                "contact_email": "office@alpha.example",
                "contact_phone": "+263771111111",
                "website": "https://alpha.example",
                "public_page_enabled": "on",
                "access_grant_max_hours": 12,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.inst.refresh_from_db()
        self.assertEqual(self.inst.name, "Alpha College Renamed")
        self.assertEqual(self.inst.type, "secondary")
        self.assertEqual(self.inst.access_grant_max_hours, 12)

        entry = PlatformAuditLog.objects.get(action="institution.edited")
        self.assertEqual(entry.before["name"], "Alpha College")
        self.assertEqual(entry.after["name"], "Alpha College Renamed")
        self.assertEqual(entry.after["access_grant_max_hours"], 12)

    def test_module_switches_need_the_manage_modules_permission(self):
        self.login(self.support)
        url = reverse("platform_admin:institution_modules", args=[self.inst.pk])
        response = self.client.post(url, {"code": "finance", "enabled": "1"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            InstitutionModule.objects.filter(institution=self.inst, code="finance").exists()
        )

        self.login(self.main)
        response = self.client.post(url, {"code": "finance", "enabled": "1"})
        self.assertEqual(response.status_code, 302)
        row = InstitutionModule.objects.get(institution=self.inst, code="finance")
        self.assertTrue(row.enabled)

        response = self.client.post(url, {"code": "finance", "enabled": "0"})
        self.assertEqual(response.status_code, 302)
        row.refresh_from_db()
        self.assertFalse(row.enabled)
        self.assertEqual(
            PlatformAuditLog.objects.filter(action="institution.module_changed").count(), 2
        )

    def test_unknown_module_codes_are_not_found(self):
        self.login(self.main)
        response = self.client.post(
            reverse("platform_admin:institution_modules", args=[self.inst.pk]),
            {"code": "not_a_module", "enabled": "1"},
        )
        self.assertEqual(response.status_code, 404)

    def test_institution_admins_can_be_deactivated_and_reactivated(self):
        self.login(self.support)
        url = reverse("platform_admin:institution_admins", args=[self.inst.pk])
        self.assertEqual(self.client.get(url).status_code, 403)

        self.login(self.main)
        response = self.client.post(
            url, {"action": "deactivate", "user": self.inst_admin.pk}
        )
        self.assertEqual(response.status_code, 302)
        self.inst_admin.refresh_from_db()
        self.assertFalse(self.inst_admin.is_active)
        self.assertTrue(
            PlatformAuditLog.objects.filter(action="institution.admin_deactivate").exists()
        )

        self.client.post(url, {"action": "activate", "user": self.inst_admin.pk})
        self.inst_admin.refresh_from_db()
        self.assertTrue(self.inst_admin.is_active)

    def test_an_institution_admin_from_another_school_cannot_be_selected(self):
        self.login(self.main)
        response = self.client.post(
            reverse("platform_admin:institution_admins", args=[self.inst.pk]),
            {"action": "deactivate", "user": self.other_admin.pk},
        )
        self.assertEqual(response.status_code, 404)
        self.other_admin.refresh_from_db()
        self.assertTrue(self.other_admin.is_active)


class PlatformBillingTests(PlatformTestBase):
    def test_billing_can_create_a_plan_but_support_cannot(self):
        payload = {
            "code": "premium",
            "name": "Premium",
            "description": "Everything, including research.",
            "monthly_price": "299.00",
            "annual_price": "2990.00",
            "currency": "USD",
            "trial_days": 30,
            "max_students": 30000,
            "max_staff": 3000,
            "modules": ["core_academics", "finance"],
            "is_active": "on",
            "ordering": 4,
        }
        self.login(self.support)
        response = self.client.post(reverse("platform_admin:plans"), payload)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Plan.objects.filter(code="premium").exists())

        self.login(self.billing)
        response = self.client.post(reverse("platform_admin:plans"), payload)
        self.assertEqual(response.status_code, 302)
        plan = Plan.objects.get(code="premium")
        self.assertEqual(plan.monthly_price, Decimal("299.00"))
        self.assertEqual(plan.modules, ["core_academics", "finance"])
        self.assertTrue(PlatformAuditLog.objects.filter(action="plan.created").exists())

    def test_a_plan_can_be_edited_and_the_change_is_audited(self):
        self.login(self.billing)
        plan = self.plans["starter"]
        response = self.client.post(
            reverse("platform_admin:plan_detail", args=[plan.pk]),
            {
                "code": plan.code,
                "name": "Starter Plus",
                "description": plan.description,
                "monthly_price": "59.00",
                "annual_price": "590.00",
                "currency": "USD",
                "trial_days": plan.trial_days,
                "max_students": plan.max_students,
                "max_staff": plan.max_staff,
                "modules": plan.modules,
                "is_active": "on",
                "ordering": plan.ordering,
            },
        )
        self.assertEqual(response.status_code, 302)
        plan.refresh_from_db()
        self.assertEqual(plan.name, "Starter Plus")
        self.assertEqual(plan.monthly_price, Decimal("59.00"))
        entry = PlatformAuditLog.objects.get(action="plan.edited")
        self.assertEqual(entry.before["name"], "Starter")
        self.assertEqual(entry.after["name"], "Starter Plus")

    def test_a_subscription_can_be_created_for_an_institution(self):
        self.login(self.billing)
        response = self.client.post(
            reverse("platform_admin:subscriptions"),
            {
                "institution": self.inst.pk,
                "plan": self.plans["standard"].pk,
                "status": Subscription.Status.ACTIVE,
                "billing_cycle": Subscription.Cycle.ANNUAL,
                "seats": 120,
                "notes": "Paid annually.",
            },
        )
        self.assertEqual(response.status_code, 302)
        subscription = Subscription.objects.get(institution=self.inst)
        self.assertEqual(subscription.plan, self.plans["standard"])
        self.assertEqual(subscription.status, Subscription.Status.ACTIVE)
        self.assertEqual(subscription.billing_cycle, Subscription.Cycle.ANNUAL)
        self.assertEqual(subscription.seats, 120)

    def test_recording_a_full_payment_marks_the_invoice_paid(self):
        self.login(self.billing)
        response = self.client.post(
            reverse("platform_admin:invoices"),
            {
                "institution": self.inst.pk,
                "number": "INV-2026-0001",
                "description": "Annual subscription",
                "amount": "490.00",
                "currency": "USD",
                "status": PlatformInvoice.Status.SENT,
                "issued_on": "2026-01-05",
                "due_on": "2026-01-31",
            },
        )
        self.assertEqual(response.status_code, 302)
        invoice = PlatformInvoice.objects.get(number="INV-2026-0001")
        self.assertEqual(invoice.amount, Decimal("490.00"))

        response = self.client.post(
            reverse("platform_admin:invoice_detail", args=[invoice.pk]),
            {
                "invoice": invoice.pk,
                "amount": "490.00",
                "method": PlatformPayment.Method.BANK,
                "reference": "BANK-1",
                "paid_at": "2026-01-20T10:30",
            },
        )
        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, PlatformInvoice.Status.PAID)
        self.assertIsNotNone(invoice.paid_on)
        payment = PlatformPayment.objects.get(invoice=invoice)
        self.assertEqual(payment.recorded_by, self.billing)
        self.assertTrue(PlatformAuditLog.objects.filter(action="payment.recorded").exists())

        response = self.client.get(reverse("platform_admin:payments"))
        self.assertContains(response, "490.00")

    def test_billing_cannot_open_operational_health_pages(self):
        self.login(self.billing)
        for name in ("health", "usage", "errors"):
            response = self.client.get(reverse(f"platform_admin:{name}"))
            self.assertEqual(response.status_code, 403, name)

    def test_support_cannot_manage_billing(self):
        self.login(self.support)
        for name in ("plans", "subscriptions", "invoices", "payments"):
            response = self.client.get(reverse(f"platform_admin:{name}"))
            self.assertEqual(response.status_code, 403, name)


class PlatformStaffAndRoleTests(PlatformTestBase):
    def test_only_a_main_admin_reaches_staff_and_roles(self):
        for user in (self.support, self.billing, self.compliance):
            self.login(user)
            for name in ("staff", "roles"):
                response = self.client.get(reverse(f"platform_admin:{name}"))
                self.assertEqual(response.status_code, 403, name)

        self.login(self.main)
        self.assertEqual(self.client.get(reverse("platform_admin:staff")).status_code, 200)
        self.assertEqual(self.client.get(reverse("platform_admin:roles")).status_code, 200)

    def test_a_support_colleague_can_be_added_and_given_a_role(self):
        self.login(self.main)
        response = self.client.post(
            reverse("platform_admin:staff"),
            {
                "email": "new.support@edusphere.example",
                "first_name": "Nyasha",
                "last_name": "Moyo",
                "role": self.roles["support"].pk,
                "is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        staff = PlatformStaff.objects.get(user__email="new.support@edusphere.example")
        self.assertEqual(staff.role, self.roles["support"])
        self.assertFalse(staff.is_main_admin)
        self.assertFalse(staff.user.has_usable_password())
        self.assertTrue(PlatformAuditLog.objects.filter(action="platform_staff.saved").exists())

    def test_a_main_admin_cannot_deactivate_their_own_account(self):
        self.login(self.main)
        response = self.client.post(
            reverse("platform_admin:staff_detail", args=[self.main_staff.pk]),
            {"action": "deactivate"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.main_staff.refresh_from_db()
        self.assertTrue(self.main_staff.is_active)
        self.assertContains(response, "cannot deactivate your own")

    def test_a_system_role_keeps_its_permissions(self):
        self.login(self.main)
        support_role = self.roles["support"]
        before = list(support_role.permissions)
        response = self.client.post(
            reverse("platform_admin:role_detail", args=[support_role.pk]),
            {"permissions": ["platform.manage_settings"]},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        support_role.refresh_from_db()
        self.assertEqual(support_role.permissions, before)
        self.assertContains(response, "Built-in roles")

    def test_a_custom_role_can_be_narrowed_and_drives_the_screens(self):
        role = PlatformRole.objects.create(
            code="viewer",
            name="Viewer",
            permissions=["platform.view_institutions", "platform.view_inbox"],
        )
        viewer = self.main.__class__.objects.create_user(
            "platformviewer",
            email="viewer@edusphere.example",
            password=STRONG_PASSWORD,
            role="staff",
            institution=self.inst,
        )
        PlatformStaff.objects.create(user=viewer, role=role)

        self.login(viewer)
        self.assertEqual(
            self.client.get(reverse("platform_admin:institutions")).status_code, 200
        )
        self.assertEqual(
            self.client.get(reverse("platform_admin:applications")).status_code, 403
        )

        self.login(self.main)
        response = self.client.post(
            reverse("platform_admin:role_detail", args=[role.pk]),
            {"permissions": ["platform.view_institutions"]},
        )
        self.assertEqual(response.status_code, 302)
        role.refresh_from_db()
        self.assertEqual(role.permissions, ["platform.view_institutions"])

        self.login(viewer)
        self.assertEqual(
            self.client.get(reverse("platform_admin:inbox")).status_code, 403
        )
        self.assertEqual(
            self.client.get(reverse("platform_admin:institutions")).status_code, 200
        )


class PlatformOperationsTests(PlatformTestBase):
    def test_support_sees_operational_summaries_without_protected_records(self):
        self.login(self.support)
        response = self.client.get(reverse("platform_admin:health"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Verified")
        self.assertContains(response, "carry no student, staff, health, finance or results")

        response = self.client.get(reverse("platform_admin:usage"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Counts only")
        self.assertNotContains(response, self.teacher.email)

        response = self.client.get(reverse("platform_admin:errors"))
        self.assertEqual(response.status_code, 200)

    def test_compliance_reads_the_audit_log_but_support_cannot(self):
        self.login(self.support)
        self.assertEqual(
            self.client.get(reverse("platform_admin:audit_log")).status_code, 403
        )

        self.login(self.compliance)
        response = self.client.get(reverse("platform_admin:audit_log"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "hash chain is intact")

        response = self.client.get(reverse("platform_admin:audit_export"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment;", response["Content-Disposition"])

    def test_backups_need_the_backup_permission(self):
        self.login(self.support)
        self.assertEqual(
            self.client.get(reverse("platform_admin:backups")).status_code, 403
        )

        self.login(self.main)
        self.assertEqual(
            self.client.post(
                reverse("platform_admin:backups"), {"note": "Nightly run"}
            ).status_code,
            302,
        )
        self.assertTrue(PlatformAuditLog.objects.filter(action="backup.requested").exists())

    def test_an_announcement_notifies_institution_administrators(self):
        self.login(self.main)
        response = self.client.post(
            reverse("platform_admin:announcements"),
            {
                "title": "Scheduled maintenance on Sunday",
                "body": "EduSphere will be read-only between 02:00 and 04:00.",
                "audience": PlatformAnnouncement.Audience.INSTITUTIONS,
                "is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            PlatformAnnouncement.objects.filter(title="Scheduled maintenance on Sunday").exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                user=self.inst_admin,
                category="announcement",
                title="Scheduled maintenance on Sunday",
            ).exists()
        )
        # Every active institution administrator is told, not only the first.
        self.assertTrue(
            Notification.objects.filter(
                user=self.other_admin,
                category="announcement",
                title="Scheduled maintenance on Sunday",
            ).exists()
        )

    def test_platform_settings_are_saved_and_show_the_legal_pages(self):
        self.login(self.main)
        response = self.client.post(
            reverse("platform_admin:settings"),
            {
                "email": "From: EduSphere <hello@edusphere.example>",
                "branding": "EduSphere",
                "legal": "Terms v2, privacy v2",
                "support": "support@edusphere.example",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            PlatformSetting.get("email")["value"],
            "From: EduSphere <hello@edusphere.example>",
        )
        self.assertTrue(PlatformAuditLog.objects.filter(action="platform_settings.saved").exists())

        response = self.client.get(reverse("platform_admin:settings"))
        self.assertContains(response, "Data processing statement")
        self.assertContains(response, "Platform privacy notice")
        self.assertContains(response, "A lawyer should review")

    def test_a_non_main_admin_cannot_open_platform_settings(self):
        self.login(self.compliance)
        self.assertEqual(
            self.client.get(reverse("platform_admin:settings")).status_code, 403
        )

    def test_the_dashboard_shows_operating_counts_and_the_audit_state(self):
        PlatformRequest.objects.create(
            kind=PlatformRequest.Kind.DATA_EXPORT,
            title="Export the alumni list",
            institution=self.inst,
        )
        self.login(self.main)
        response = self.client.get(reverse("platform_admin:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["chain_ok"])
        self.assertEqual(
            response.context["institution_count"],
            Institution.objects.filter(deleted_at__isnull=True).count(),
        )
        self.assertEqual(response.context["open_request_count"], 1)

    def test_platform_requests_can_be_decided_from_the_inbox(self):
        item = PlatformRequest.objects.create(
            kind=PlatformRequest.Kind.PLAN_CHANGE,
            title="Move to the College plan",
            institution=self.inst,
        )
        self.login(self.support)
        response = self.client.post(
            reverse("platform_admin:platform_request_detail", args=[item.pk]),
            {"action": "approve", "reason": "Approved for the new academic year."},
        )
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.status, PlatformRequest.Status.APPROVED)
        self.assertEqual(item.decision_reason, "Approved for the new academic year.")
        self.assertTrue(
            PlatformAuditLog.objects.filter(action="platform_request.approve").exists()
        )

    def test_acting_without_a_platform_row_is_forbidden(self):
        self.login(self.teacher)
        for name in ("dashboard", "institutions", "audit_log", "health"):
            response = self.client.get(reverse(f"platform_admin:{name}"))
            self.assertEqual(response.status_code, 403, name)

    def test_an_inactive_platform_staff_row_grants_nothing(self):
        self.support_staff.is_active = False
        self.support_staff.save(update_fields=["is_active"])
        self.forget_platform_cache(self.support)
        self.client.force_login(self.support)
        response = self.client.get(reverse("platform_admin:dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_a_platform_admin_never_sees_an_institution_consent_page(self):
        self.login(self.main)
        for name in (
            "institution_access_requests",
            "institution_data_access",
            "institution_consent_log",
        ):
            response = self.client.get(reverse(f"platform_admin:{name}"))
            self.assertEqual(response.status_code, 403, name)
