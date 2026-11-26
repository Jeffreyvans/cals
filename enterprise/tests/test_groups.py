"""Part 1: organisation structure, scoped roles, aggregates and inheritance."""

import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from academics.factories import (
    AttendanceFactory,
    EnrollmentFactory,
    ProgrammeFactory,
    StudentFactory,
)
from core.models import AuditEntry, Institution
from core.modules import set_module
from core.roles import ensure_roles
from core.tenancy import use_institution
from enterprise.access import GroupRole
from enterprise.models import (
    CampusSettingOverride,
    GroupBilling,
    GroupInvoice,
    GroupInvoiceLine,
    Organisation,
    OrganisationMembership,
)
from enterprise.services import (
    attach_institution,
    campus_aggregates,
    create_consolidated_invoice,
    create_organisation,
    detach_institution,
    effective_setting,
    refresh_group_alerts,
    request_group_student_access,
    save_campus_override,
    save_shared_setting,
)
from finance.models import CollectionTarget, Invoice, Payment
from platform_admin.access import grant_covers
from platform_admin.models import (
    AccessRequest,
    Plan,
    PlatformAuditLog,
    PlatformInvoice,
    PlatformStaff,
    Subscription,
)
from platform_admin.services import (
    active_grant_for,
    decide_access_request,
    ensure_platform_roles,
    revoke_grant,
)

User = get_user_model()


class GroupBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.first = Institution.objects.create(
            name="Riverside Campus", slug="riverside-groups", type="secondary"
        )
        cls.second = Institution.objects.create(
            name="Hilltop Campus", slug="hilltop-groups", type="college"
        )
        for institution in (cls.first, cls.second):
            set_module(institution, "groups", True)
            ensure_roles(institution)
        cls.org = create_organisation(
            name="Riverside Trust", slug="riverside-trust", org_type="trust"
        )
        attach_institution(cls.org, cls.first, campus_label="North campus", sort_order=1)
        attach_institution(cls.org, cls.second, campus_label="South campus", sort_order=2)
        cls.group_admin = User.objects.create_user(
            "groupboss", password="pw123456", role="admin", institution=cls.first,
            is_superuser=True,
        )
        cls.group_auditor = User.objects.create_user(
            "groupauditor", password="pw123456", role="staff", institution=cls.second
        )
        cls.campus_admin = User.objects.create_user(
            "campusboss", password="pw123456", role="admin", institution=cls.first,
            is_superuser=True,
        )
        cls.other_org = create_organisation(
            name="Another Trust", slug="another-trust", org_type="company"
        )
        cls.other_admin = User.objects.create_user(
            "otherboss", password="pw123456", role="admin", institution=cls.second,
            is_superuser=True,
        )
        assign_role(cls.org, cls.group_admin, GroupRole.ADMIN)
        assign_role(cls.org, cls.group_auditor, GroupRole.AUDITOR)


def assign_role(org, user, role):
    from enterprise.services import assign_group_role

    return assign_group_role(org, user, role)


class OrganisationStructureTests(GroupBase):
    def test_organisation_creation_and_attach_detach_keep_campus_data(self):
        with use_institution(self.first):
            student = StudentFactory(student_no="G-1001")
            self.assertEqual(OrganisationMembership.objects.count(), 2)
            self.assertEqual(self.first.group_membership.organisation, self.org)
            self.assertTrue(detach_institution(self.org, self.first))
            self.assertFalse(OrganisationMembership.objects.filter(
                organisation=self.org, institution=self.first
            ).exists())
            self.assertTrue(Institution.objects.filter(pk=self.first.pk).exists())
            self.assertEqual(StudentFactory._meta.model.all_objects.filter(
                pk=student.pk
            ).count(), 1)

    def test_an_institution_can_belong_to_at_most_one_organisation(self):
        with self.assertRaises(ValidationError):
            attach_institution(self.other_org, self.first)
        self.assertEqual(
            OrganisationMembership.objects.filter(institution=self.first).count(), 1
        )

    def test_platform_admin_can_create_and_manage_an_organisation(self):
        PlatformStaff.objects.create(
            user=self.group_admin,
            role=ensure_platform_roles()["main_admin"],
            is_active=True,
        )
        self.client.force_login(self.group_admin)
        response = self.client.post(
            reverse("enterprise:organisation_create"),
            {"name": "New Trust", "slug": "new-trust", "type": "church",
             "contact_email": "office@new.example"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Organisation.objects.filter(slug="new-trust").exists())
        detail = self.client.get(
            reverse("enterprise:organisation_detail",
                    args=[Organisation.objects.get(slug="new-trust").pk])
        )
        self.assertEqual(detail.status_code, 200)


class GroupRoleTests(GroupBase):
    def test_auditor_sees_only_aggregates_and_no_student_names(self):
        self.client.force_login(self.group_auditor)
        with use_institution(self.first):
            student = StudentFactory(
                programme=ProgrammeFactory(code="GRP-P1"), student_no="SECRET-STUDENT"
            )
        response = self.client.get(reverse("enterprise:group_dashboard", args=[self.org.slug]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn(student.user.get_full_name(), content)
        self.assertNotIn("SECRET-STUDENT", content)
        self.assertContains(response, "Aggregate figures only")
        post = self.client.post(
            reverse("enterprise:group_setting_save", args=[self.org.slug]),
            {"key": "terminology", "value": "{}"},
        )
        self.assertEqual(post.status_code, 403)

    def test_student_pages_require_an_approved_grant(self):
        self.client.force_login(self.group_admin)
        denied = self.client.get(reverse("enterprise:group_students", args=[self.org.slug]))
        self.assertEqual(denied.status_code, 403)
        request = request_group_student_access(
            self.group_admin, self.org, self.first,
            "Need enrolment figures for the quarterly trustees report.",
            categories=("personal_data",),
        )
        self.assertEqual(request.status, AccessRequest.Status.PENDING)
        allowed = self.client.get(reverse("enterprise:group_students", args=[self.org.slug]))
        self.assertEqual(allowed.status_code, 403)
        decision = decide_access_request(
            request, self.campus_admin, True,
            categories=["personal_data"], hours=24,
            mode=AccessRequest.Mode.READ,
        )
        self.assertEqual(decision.status, AccessRequest.Status.APPROVED)
        grant = active_grant_for(self.group_admin, self.first)
        self.assertTrue(grant_covers(grant, {"personal_data"}))
        granted = self.client.get(reverse("enterprise:group_students", args=[self.org.slug]))
        self.assertEqual(granted.status_code, 200)
        self.assertTrue(PlatformAuditLog.objects.filter(
            institution=self.first, action="platform.view", actor=self.group_admin
        ).exists())
        revoke_grant(grant, self.campus_admin, reason="No longer needed.")
        denied_again = self.client.get(
            reverse("enterprise:group_students", args=[self.org.slug])
        )
        self.assertEqual(denied_again.status_code, 403)

    def test_group_roles_are_scoped_to_one_organisation(self):
        self.assertIsNone(
            self.group_auditor.group_memberships.filter(organisation=self.other_org).first()
        )
        response = self.client.get(
            reverse("enterprise:group_dashboard", args=[self.other_org.slug])
        )
        self.assertIn(response.status_code, (302, 403))
        self.client.force_login(self.group_admin)
        self.assertEqual(
            self.client.get(reverse("enterprise:group_dashboard",
                                    args=[self.other_org.slug])).status_code,
            403,
        )

    def test_access_log_entries_do_not_contain_secrets(self):
        request = request_group_student_access(
            self.group_admin, self.org, self.first,
            "Need enrolment figures for the quarterly trustees report.",
        )
        decide_access_request(
            request, self.campus_admin, True, categories=["personal_data"],
            hours=24, mode=AccessRequest.Mode.READ,
        )
        self.client.force_login(self.group_admin)
        self.client.get(reverse("enterprise:group_students", args=[self.org.slug]))
        for entry in PlatformAuditLog.objects.filter(institution=self.first):
            blob = str(entry.detail) + str(entry.before) + str(entry.after)
            self.assertNotIn("password", blob.lower())
            self.assertNotIn("pw123456", blob)
            self.assertNotIn("token", blob.lower())


class SharedSettingTests(GroupBase):
    def test_inheritance_locking_and_no_record_rewrites(self):
        with use_institution(self.first):
            student = StudentFactory(student_no="INHERIT-1")
            original_name = student.user.get_full_name()
        save_shared_setting(
            self.org, "terminology", {"pupil_label": "Learner"}, actor=self.group_admin
        )
        self.assertEqual(
            effective_setting(self.org, self.first, "terminology")["label"],
            "Inherited from group",
        )
        save_campus_override(
            self.first, "terminology", {"pupil_label": "Student"}, actor=self.campus_admin
        )
        self.assertEqual(
            effective_setting(self.org, self.first, "terminology")["label"],
            "Set by this campus",
        )
        save_shared_setting(
            self.org, "terminology", {"pupil_label": "Pupil"},
            actor=self.group_admin, lock=True,
        )
        self.assertEqual(
            effective_setting(self.org, self.first, "terminology")["label"],
            "Inherited from group",
        )
        with self.assertRaises(ValidationError):
            save_campus_override(
                self.first, "terminology", {"pupil_label": "Other"},
                actor=self.campus_admin,
            )
        with use_institution(self.first):
            student.refresh_from_db()
            self.assertEqual(student.user.get_full_name(), original_name)
            self.assertEqual(student.student_no, "INHERIT-1")
        self.assertTrue(CampusSettingOverride.objects.filter(institution=self.first).exists())

    def test_group_default_change_does_not_rewrite_a_campus_record(self):
        with use_institution(self.first):
            student = StudentFactory(student_no="NO-REWRITE")
        save_shared_setting(self.org, "grading_scale", {"pass_mark": 50})
        with use_institution(self.first):
            student.refresh_from_db()
            self.assertEqual(student.student_no, "NO-REWRITE")
        self.assertTrue(AuditEntry.objects.filter(action="group.setting_changed").exists())


class DashboardAndBillingTests(GroupBase):
    def test_dashboard_figures_match_the_campus_rows(self):
        with use_institution(self.first):
            student = StudentFactory()
            invoice = Invoice.objects.create(
                student=student, description="Tuition", amount=Decimal("200.00"),
                due_date=timezone.localdate(),
            )
            Payment.objects.create(invoice=invoice, amount=Decimal("120.00"))
            CollectionTarget.objects.create(
                name="Term target", amount=Decimal("400.00"),
                start_date=timezone.localdate(), end_date=timezone.localdate(),
            )
            enrollment = EnrollmentFactory(student=student)
            AttendanceFactory(enrollment=enrollment, present=True)
            AttendanceFactory(
                enrollment=enrollment, status="absent",
                date=timezone.localdate() + timezone.timedelta(days=1),
            )
            from core.audit import log_action
            log_action(self.group_admin, "junk", after={"password": "do-not-store"})
        figures = campus_aggregates(self.first)
        self.assertEqual(figures["enrolment"], 1)
        self.assertEqual(figures["collected"], Decimal("120.00"))
        self.assertEqual(figures["target"], Decimal("400.00"))
        self.assertEqual(figures["attendance"], 50.0)
        self.client.force_login(self.group_admin)
        response = self.client.get(reverse("enterprise:group_dashboard", args=[self.org.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "120 / 400.00")
        self.assertNotIn("do-not-store", response.content.decode())
        for fmt, magic in (("xlsx", b"PK"), ("pdf", b"%PDF"), ("csv", b"Campus")):
            export = self.client.get(
                reverse("enterprise:group_export", args=[self.org.slug]) + f"?format={fmt}"
            )
            self.assertEqual(export.status_code, 200)
            self.assertTrue(export.content.startswith(magic))

    def test_consolidated_billing_uses_per_campus_lines_and_volume_discount(self):
        plan, _ = Plan.objects.get_or_create(
            code="college",
            defaults={
                "name": "College",
                "monthly_price": Decimal("100.00"),
                "modules": ["groups"],
            },
        )
        plan.monthly_price = Decimal("100.00")
        plan.modules = ["groups"]
        plan.save(update_fields=["monthly_price", "modules"])
        Subscription.objects.create(institution=self.first, plan=plan, status="active")
        Subscription.objects.create(institution=self.second, plan=plan, status="active")
        billing, _ = GroupBilling.objects.get_or_create(organisation=self.org)
        billing.mode = GroupBilling.Mode.CONSOLIDATED
        billing.save(update_fields=["mode"])
        invoice = create_consolidated_invoice(self.org, actor=self.group_admin, period="2026-11")
        self.assertEqual(invoice.amount, Decimal("200.00"))
        self.assertEqual(invoice.discount, Decimal("20.00"))
        self.assertEqual(invoice.total, Decimal("180.00"))
        self.assertEqual(invoice.lines.count(), 2)
        self.assertEqual(
            sum((line.net for line in GroupInvoiceLine.objects.filter(invoice=invoice)),
                Decimal("0")),
            Decimal("180.00"),
        )
        self.assertEqual(GroupInvoice.objects.count(), 1)
        self.assertIsNotNone(invoice.platform_invoice)
        self.assertEqual(invoice.platform_invoice.institution_id, self.first.pk)

    def test_separate_billing_does_not_create_a_consolidated_invoice(self):
        GroupBilling.objects.create(organisation=self.org, mode=GroupBilling.Mode.SEPARATE)
        with self.assertRaises(ValidationError):
            create_consolidated_invoice(self.org, actor=self.group_admin)


class EnterpriseHardeningTests(GroupBase):
    def test_export_uses_the_same_filters_as_the_dashboard(self):
        self.client.force_login(self.group_admin)
        url = reverse("enterprise:group_dashboard", args=[self.org.slug])
        export_url = reverse("enterprise:group_export", args=[self.org.slug])
        dashboard = self.client.get(url, {"campus": [self.first.pk], "term": "abc"})
        self.assertEqual(dashboard.status_code, 200)
        # The campus chooser lists every campus, so compare the filtered rows,
        # not the whole page.
        self.assertEqual(
            [row["campus"] for row in dashboard.context["dashboard"]["rows"]],
            [self.first.name],
        )
        export = self.client.get(
            export_url, {"format": "csv", "campus": [self.first.pk], "term": "abc"}
        )
        self.assertEqual(export.status_code, 200)
        body = export.content.decode()
        self.assertIn("Riverside Campus", body)
        self.assertNotIn("Hilltop Campus", body)

    def test_settings_are_valid_json_and_campus_admin_can_use_the_screen(self):
        self.client.force_login(self.group_admin)
        saved = self.client.post(
            reverse("enterprise:group_setting_save", args=[self.org.slug]),
            {"key": "terminology", "value": json.dumps({"pupil_label": "Learner"})},
        )
        self.assertEqual(saved.status_code, 302)
        page = self.client.get(reverse("enterprise:group_settings", args=[self.org.slug]))
        self.assertContains(page, "pupil_label")
        bad = self.client.post(
            reverse("enterprise:group_setting_save", args=[self.org.slug]),
            {"key": "terminology", "value": "{'bad': 'json'}"},
        )
        self.assertEqual(bad.status_code, 302)

        self.client.force_login(self.campus_admin)
        screen = self.client.get(reverse(
            "enterprise:campus_settings", args=[self.org.slug, self.first.pk]
        ))
        self.assertEqual(screen.status_code, 200)
        override = self.client.post(
            reverse("enterprise:campus_setting_save", args=[self.org.slug, self.first.pk]),
            {"key": "terminology", "value": json.dumps({"pupil_label": "Student"})},
        )
        self.assertEqual(override.status_code, 302)
        self.assertEqual(
            effective_setting(self.org, self.first, "terminology")["label"],
            "Set by this campus",
        )

    def test_group_switch_rejects_an_external_redirect(self):
        self.client.force_login(self.group_admin)
        response = self.client.post(
            reverse("enterprise:group_switch_campus", args=[self.org.slug]),
            {"campus": self.first.pk, "next": "https://evil.example/steal"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("evil.example", response["Location"])

    def test_overrides_do_not_follow_a_campus_to_another_organisation(self):
        save_shared_setting(self.org, "terminology", {"pupil_label": "Group one"})
        save_campus_override(self.first, "terminology", {"pupil_label": "Campus one"})
        detach_institution(self.org, self.first)
        attach_institution(self.other_org, self.first)
        save_campus_override(self.first, "terminology", {"pupil_label": "Group two"})
        self.assertEqual(
            effective_setting(self.org, self.first, "terminology")["value"],
            {"pupil_label": "Group one"},
        )
        self.assertEqual(
            effective_setting(self.other_org, self.first, "terminology")["value"],
            {"pupil_label": "Group two"},
        )

    def test_explicit_zero_discount_and_rounded_lines_agree_with_the_invoice(self):
        third = Institution.objects.create(
            name="Valley Campus", slug="valley-groups", type="primary"
        )
        set_module(third, "groups", True)
        ensure_roles(third)
        attach_institution(self.org, third)
        plan, _ = Plan.objects.get_or_create(
            code="college",
            defaults={"name": "College", "monthly_price": Decimal("100.00")},
        )
        plan.monthly_price = Decimal("100.00")
        plan.save(update_fields=["monthly_price"])
        for campus in self.org.campuses():
            Subscription.objects.create(institution=campus, plan=plan, status="active")
        billing, _ = GroupBilling.objects.get_or_create(organisation=self.org)
        billing.mode = GroupBilling.Mode.CONSOLIDATED
        billing.discount_rate = Decimal("33.33")
        billing.save(update_fields=["mode", "discount_rate"])

        rounded = create_consolidated_invoice(self.org, actor=self.group_admin)
        self.assertEqual(
            sum((line.net for line in rounded.lines.all()), Decimal("0")), rounded.total
        )
        billing.discount_rate = Decimal("0")
        billing.save(update_fields=["discount_rate"])
        free = create_consolidated_invoice(self.org, actor=self.group_admin)
        self.assertEqual(free.discount, Decimal("0.00"))
        self.assertEqual(free.total, Decimal("300.00"))

    def test_refreshing_alerts_preserves_an_open_alert(self):
        with use_institution(self.first):
            student = StudentFactory()
            invoice = Invoice.objects.create(
                student=student, description="Fees", amount=Decimal("200.00"),
                due_date=timezone.localdate(),
            )
            Payment.objects.create(invoice=invoice, amount=Decimal("10.00"))
            CollectionTarget.objects.create(
                name="Term target", amount=Decimal("400.00"),
                start_date=timezone.localdate(), end_date=timezone.localdate(),
            )
        first = refresh_group_alerts(self.org)
        self.assertTrue(first.exists())
        created = first.first().created
        second = refresh_group_alerts(self.org)
        self.assertEqual(second.count(), first.count())
        self.assertEqual(second.first().created, created)

    def test_platform_cannot_issue_a_separate_invoice_for_a_consolidated_campus(self):
        PlatformStaff.objects.create(
            user=self.group_admin,
            role=ensure_platform_roles()["main_admin"],
            is_active=True,
        )
        billing, _ = GroupBilling.objects.get_or_create(organisation=self.org)
        billing.mode = GroupBilling.Mode.CONSOLIDATED
        billing.save(update_fields=["mode"])
        self.client.force_login(self.group_admin)
        response = self.client.post(
            reverse("platform_admin:invoices"),
            {
                "institution": self.first.pk, "number": "PLAT-DOUBLE",
                "description": "Tuition", "amount": "100.00", "currency": "USD",
                "status": "sent", "issued_on": timezone.localdate().isoformat(),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PlatformInvoice.objects.filter(number="PLAT-DOUBLE").exists())
