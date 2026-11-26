"""Part 2: sponsor portal, consent levels, ledger entries and opt-in notices."""

import csv
import io
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from academics.factories import ProgrammeFactory, StudentFactory
from core.audit import AuditEntry
from core.email_types import STAFF_INVITATION
from core.models import Institution, Notification, OutboundMessage
from core.modules import set_module
from core.roles import ensure_roles
from core.tenancy import use_institution
from enterprise.access import SponsorRole, portal_accounts_for, student_level_grant
from enterprise.models import SponsorAccount
from enterprise.services import (
    create_sponsor_account,
    notify_sponsor,
    sponsor_statement,
    sponsor_student_label,
    verify_sponsor_account,
    withdraw_sponsor_sharing,
)
from finance.models import (
    Discount,
    Invoice,
    MoneyLog,
    Payment,
    Sponsor,
    Sponsorship,
    Waiver,
)
from platform_admin.models import (
    AccessRequest,
    DataAccessGrant,
    PlatformAuditLog,
    PlatformStaff,
)
from platform_admin.services import ensure_platform_roles
from platform_admin.twofa import code_at, new_secret

User = get_user_model()


class SponsorBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.school = Institution.objects.create(
            name="Grant High", slug="grant-high-sponsors", type="secondary"
        )
        set_module(cls.school, "sponsor_portal", True)
        ensure_roles(cls.school)
        with use_institution(cls.school):
            cls.sponsor = Sponsor.objects.create(
                name="Hope Foundation", email="office@hope.example", type=Sponsor.Type.NGO
            )
            cls.other_sponsor = Sponsor.objects.create(name="Another Donor")
            programme = ProgrammeFactory(code="SPON-P1")
            cls.student = StudentFactory(
                programme=programme, student_no="SP-1001",
                user__first_name="Chipo", user__last_name="Dlamini",
            )
            cls.student.user.first_name = "Chipo"
            cls.student.user.last_name = "Dlamini"
            cls.student.user.save(update_fields=["first_name", "last_name"])
            cls.invoice = Invoice.objects.create(
                student=cls.student, description="Tuition — Chipo Dlamini",
                amount=Decimal("400.00"), due_date=date.today(),
            )
            Sponsorship.objects.create(
                sponsor=cls.sponsor, student=cls.student, percentage=Decimal("50")
            )
        cls.owner = User.objects.create_user(
            "sponsorowner", password="SponsorPass123!", email="owner@hope.example",
            role="parent", institution=cls.school,
        )
        cls.viewer = User.objects.create_user(
            "sponsorviewer", password="SponsorPass123!", role="parent",
            institution=cls.school,
        )
        cls.other_user = User.objects.create_user(
            "otherdonor", password="SponsorPass123!", role="parent",
            institution=cls.school,
        )
        cls.platform_user = User.objects.create_user(
            "sponsorplatform", password="SponsorPass123!", role="staff",
            institution=cls.school, is_superuser=True,
        )
        PlatformStaff.objects.create(
            user=cls.platform_user,
            role=ensure_platform_roles()["main_admin"],
            is_active=True,
        )
        cls.account = create_sponsor_account(
            user=cls.owner, sponsor=cls.sponsor, role=SponsorRole.ADMIN,
            sharing_level=1, actor=cls.platform_user,
        )
        cls.viewer_account = create_sponsor_account(
            user=cls.viewer, sponsor=cls.sponsor, role=SponsorRole.VIEWER,
            sharing_level=1, actor=cls.platform_user,
        )
        cls.other_account = create_sponsor_account(
            user=cls.other_user, sponsor=cls.other_sponsor, role=SponsorRole.ADMIN,
            sharing_level=1, actor=cls.platform_user,
        )
        for account in (cls.account, cls.viewer_account, cls.other_account):
            verify_sponsor_account(account.user, account.verification_token)

    def setUp(self):
        from django.core.cache import cache

        cache.clear()

    def grant(self, account=None, categories=("personal_data", "finance")):
        account = account or self.account
        request = AccessRequest.objects.create(
            institution=account.sponsor.institution,
            requested_by=account.user,
            categories=list(categories),
            reason="Sponsor consent for named scholarship reporting.",
            status=AccessRequest.Status.APPROVED,
            expires_at=timezone.now() + timedelta(days=7),
        )
        return DataAccessGrant.objects.create(
            access_request=request,
            institution=account.sponsor.institution,
            platform_admin=account.user,
            categories=list(categories),
            starts_at=timezone.now() - timedelta(minutes=1),
            expires_at=timezone.now() + timedelta(hours=2),
        )

    def make_sensitive_ready(self, account=None):
        account = account or self.account
        account.two_factor_secret = new_secret()
        account.two_factor_enabled = True
        account.last_reauth_at = timezone.now()
        account.save(update_fields=[
            "two_factor_secret", "two_factor_enabled", "last_reauth_at", "updated_at"
        ])
        return account

    def pay(self, amount="200", method="bank"):
        return self.client.post(reverse("enterprise:sponsor_pay"), {
            "invoice": self.invoice.pk, "amount": amount, "method": method,
        })


class SponsorFeatureAndAccessTests(SponsorBase):
    def test_feature_off_hides_every_portal_page(self):
        set_module(self.school, "sponsor_portal", False)
        self.client.force_login(self.owner)
        for name in (
            "sponsor_dashboard", "sponsor_invoices", "sponsor_statement",
            "sponsor_receipts", "sponsor_settings", "sponsor_security",
        ):
            response = self.client.get(reverse(f"enterprise:{name}"))
            self.assertEqual(response.status_code, 404, name)
        self.assertEqual(portal_accounts_for(self.owner), [])

    def test_anonymous_visitor_is_sent_to_the_portal_sign_in(self):
        response = self.client.get(reverse("enterprise:sponsor_dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("enterprise:sponsor_login"), response["Location"])

    def test_viewer_cannot_post_payments_or_credit_notes(self):
        self.make_sensitive_ready(self.viewer_account)
        self.client.force_login(self.viewer)
        self.assertEqual(
            self.client.get(reverse("enterprise:sponsor_invoices")).status_code, 200
        )
        self.assertEqual(self.pay().status_code, 403)
        self.assertEqual(
            self.client.post(reverse("enterprise:sponsor_credit_note"), {
                "invoice": self.invoice.pk, "amount": "10", "reason": "Reason",
            }).status_code,
            403,
        )

    def test_one_sponsor_can_never_see_another_sponsors_rows(self):
        self.client.force_login(self.other_user)
        dashboard = self.client.get(reverse("enterprise:sponsor_dashboard"))
        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, "Another Donor")
        self.assertNotContains(dashboard, "Hope Foundation")
        statement = self.client.get(reverse("enterprise:sponsor_statement"))
        self.assertNotContains(statement, "SP-1001")
        self.assertNotContains(statement, self.student.name)


class SponsorInvitationTests(SponsorBase):
    def test_platform_invitation_uses_one_time_code_and_central_email(self):
        new_user = User.objects.create_user(
            "newsponsor", password="SponsorPass123!", role="parent",
            institution=self.school, email="newsponsor@hope.example",
        )
        self.client.force_login(self.platform_user)
        response = self.client.post(reverse("enterprise:sponsor_account_create"), {
            "user": new_user.pk, "sponsor": self.sponsor.pk,
            "role": SponsorRole.FINANCE, "sharing_level": "1",
            "notify_in_app": "on", "is_active": "on",
        })
        self.assertEqual(response.status_code, 302)
        account = SponsorAccount.objects.get(user=new_user)
        message = OutboundMessage.objects.filter(
            email_type=STAFF_INVITATION, recipient=new_user.email,
        ).first()
        self.assertIsNotNone(message)
        self.assertIn(account.verification_token, message.body)
        self.assertNotIn(account.verification_token, response.content.decode())

        self.client.logout()
        self.client.force_login(new_user)
        verify_url = reverse("enterprise:sponsor_verify")
        self.assertContains(self.client.get(verify_url), "Verify your sponsor account")
        bad = self.client.post(verify_url, {"token": "not-the-code"})
        self.assertEqual(bad.status_code, 200)
        account.refresh_from_db()
        self.assertIsNone(account.verified_at)
        good = self.client.post(verify_url, {"token": account.verification_token})
        self.assertEqual(good.status_code, 302)
        account.refresh_from_db()
        self.assertIsNotNone(account.verified_at)

    def test_verification_code_cannot_be_reused(self):
        account = create_sponsor_account(
            user=User.objects.create_user(
                "reuseuser", password="SponsorPass123!", institution=self.school
            ),
            sponsor=self.sponsor, role=SponsorRole.VIEWER, sharing_level=1,
        )
        token = account.verification_token
        verify_sponsor_account(account.user, token)
        with self.assertRaises(ValidationError):
            verify_sponsor_account(account.user, token)

    def test_multi_sponsor_user_chooses_which_sponsor_to_represent(self):
        other_school = Institution.objects.create(
            name="Second School", slug="second-school-sponsors", type="primary"
        )
        set_module(other_school, "sponsor_portal", True)
        with use_institution(other_school):
            sponsor = Sponsor.objects.create(name="Second Foundation")
        second = create_sponsor_account(
            user=self.owner, sponsor=sponsor, role=SponsorRole.ADMIN, sharing_level=1,
        )
        verify_sponsor_account(self.owner, second.verification_token)
        self.client.logout()
        response = self.client.post(reverse("enterprise:sponsor_login"), {
            "username": self.owner.username, "password": "SponsorPass123!",
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("enterprise:sponsor_select"), response["Location"])
        chosen = self.client.post(reverse("enterprise:sponsor_select"), {
            "account": second.pk,
        })
        self.assertEqual(chosen.status_code, 302)
        dashboard = self.client.get(reverse("enterprise:sponsor_dashboard"))
        self.assertContains(dashboard, "Second Foundation")
        self.assertNotContains(dashboard, "Hope Foundation")


class SponsorSharingTests(SponsorBase):
    def test_levels_one_two_and_three_release_only_the_agreed_detail(self):
        self.assertEqual(
            sponsor_student_label(self.account, self.student), "Student"
        )
        self.make_sensitive_ready()
        self.account = type(self.account).objects.get(pk=self.account.pk)
        from enterprise.services import set_sharing_level

        set_sharing_level(self.account, 2, evidence="Shared coded refs for audit.")
        self.account.refresh_from_db()
        label = sponsor_student_label(self.account, self.student)
        self.assertEqual(label, f"Student {self.student.pk}")
        self.assertNotIn(self.student.name, label)

        set_sharing_level(
            self.account, 3,
            evidence="Named detail needed for the annual donor report.",
            request=None,
        )
        self.account.refresh_from_db()
        self.assertEqual(
            sponsor_student_label(self.account, self.student), f"Student {self.student.pk}"
        )
        self.grant()
        self.assertEqual(sponsor_student_label(self.account, self.student), self.student.name)

    def test_withdrawal_revokes_live_grants_immediately(self):
        grant = self.grant()
        request = AccessRequest.objects.get(pk=grant.access_request_id)
        self.assertIsNotNone(
            student_level_grant(self.owner, self.school, ("personal_data", "finance"))
        )
        self.client.force_login(self.owner)
        withdraw_sponsor_sharing(self.account, "Donor withdrew consent.", request=None)
        self.account.refresh_from_db()
        grant.refresh_from_db()
        request.refresh_from_db()
        self.assertEqual(self.account.sharing_level, 0)
        self.assertIsNotNone(self.account.withdrawn_at)
        self.assertIsNotNone(grant.revoked_at)
        self.assertEqual(request.status, AccessRequest.Status.REVOKED)
        self.assertIsNone(
            student_level_grant(self.owner, self.school, ("personal_data", "finance"))
        )
        self.assertEqual(sponsor_student_label(self.account, self.student), "Student")
        self.assertEqual(
            self.client.get(reverse("enterprise:sponsor_statement")).status_code, 200
        )

    def test_level_one_statement_and_export_never_name_a_student(self):
        self.client.force_login(self.owner)
        self.make_sensitive_ready()
        self.pay()
        statement = self.client.get(reverse("enterprise:sponsor_statement"))
        self.assertEqual(statement.status_code, 200)
        self.assertNotContains(statement, self.student.name)
        self.assertNotContains(statement, "SP-1001")
        export = self.client.get(reverse("enterprise:sponsor_export") + "?format=csv")
        self.assertEqual(export.status_code, 200)
        self.assertNotIn(self.student.name, export.content.decode())
        rows = list(csv.reader(io.StringIO(export.content.decode())))
        self.assertEqual(rows[0], ["Date", "Kind", "Reference", "Student", "Amount"])

    def test_level_two_export_uses_codes_not_names(self):
        from enterprise.services import set_sharing_level

        set_sharing_level(self.account, 2, evidence="Coded references are enough.")
        self.client.force_login(self.owner)
        self.make_sensitive_ready()
        self.pay()
        export = self.client.get(reverse("enterprise:sponsor_export") + "?format=csv")
        content = export.content.decode()
        self.assertNotIn(self.student.name, content)
        self.assertIn(f"Student {self.student.pk}", content)


class SponsorLedgerTests(SponsorBase):
    def test_payment_is_capped_and_posted_to_the_existing_ledger(self):
        self.make_sensitive_ready()
        self.client.force_login(self.owner)
        over = self.pay("250")
        self.assertEqual(over.status_code, 302)
        self.assertFalse(Payment.all_objects.filter(institution=self.school).exists())

        response = self.pay("200")
        self.assertEqual(response.status_code, 302)
        payment = Payment.all_objects.get(institution=self.school)
        self.assertEqual(payment.amount, Decimal("200.00"))
        self.assertIn(f"SPONSOR-{self.sponsor.pk}-", payment.reference)
        self.assertTrue(MoneyLog.objects.filter(
            action=MoneyLog.Action.PAYMENT, invoice=self.invoice,
        ).exists())
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.balance, Decimal("200.00"))
        self.assertEqual(sponsor_statement(self.account)["totals"]["paid"], Decimal("200.00"))

    def test_credit_note_is_capped_and_cannot_be_edited_or_deleted(self):
        self.make_sensitive_ready()
        self.client.force_login(self.owner)
        over = self.client.post(reverse("enterprise:sponsor_credit_note"), {
            "invoice": self.invoice.pk, "amount": "250", "reason": "Fees forgiven",
        })
        self.assertEqual(over.status_code, 302)
        self.assertFalse(Waiver.all_objects.filter(institution=self.school).exists())

        response = self.client.post(reverse("enterprise:sponsor_credit_note"), {
            "invoice": self.invoice.pk, "amount": "50", "reason": "Fees forgiven",
        })
        self.assertEqual(response.status_code, 302)
        waiver = Waiver.all_objects.get(institution=self.school)
        self.assertEqual(waiver.amount, Decimal("50.00"))
        self.assertTrue(MoneyLog.objects.filter(
            action=MoneyLog.Action.WAIVER, invoice=self.invoice,
        ).exists())
        self.assertEqual(
            sponsor_statement(self.account)["totals"]["credit_note"], Decimal("50.00")
        )
        waiver.amount = Decimal("60.00")
        with self.assertRaises(ValidationError):
            waiver.save()
        with self.assertRaises(ValidationError):
            waiver.delete()

    def test_sensitive_actions_need_two_factor_and_recent_password(self):
        self.client.force_login(self.owner)
        blocked = self.pay("10")
        self.assertEqual(blocked.status_code, 403)

        self.account.two_factor_secret = new_secret()
        self.account.save(update_fields=["two_factor_secret", "updated_at"])
        self.assertEqual(self.pay("10").status_code, 403)

        self.account.two_factor_enabled = True
        self.account.last_reauth_at = timezone.now() - timedelta(minutes=30)
        self.account.save(update_fields=[
            "two_factor_enabled", "last_reauth_at", "updated_at"
        ])
        self.assertEqual(self.pay("10").status_code, 403)

        reauth = self.client.post(reverse("enterprise:sponsor_security"), {
            "action": "reauth", "password": "SponsorPass123!",
        })
        self.assertEqual(reauth.status_code, 302)
        self.assertEqual(self.pay("10").status_code, 302)
        self.assertTrue(Payment.all_objects.filter(institution=self.school).exists())

    def test_authenticator_setup_can_be_enabled_from_security_page(self):
        self.client.force_login(self.owner)
        started = self.client.post(reverse("enterprise:sponsor_security"), {
            "action": "start",
        })
        self.assertEqual(started.status_code, 302)
        self.account.refresh_from_db()
        self.assertFalse(self.account.two_factor_enabled)
        self.assertTrue(self.account.two_factor_secret)
        enabled = self.client.post(reverse("enterprise:sponsor_security"), {
            "action": "enable",
            "code": code_at(self.account.two_factor_secret),
        })
        self.assertEqual(enabled.status_code, 302)
        self.account.refresh_from_db()
        self.assertTrue(self.account.two_factor_enabled)

    def test_receipt_pdf_is_redacted_until_named_access_is_live(self):
        self.make_sensitive_ready()
        self.client.force_login(self.owner)
        self.pay()
        payment = Payment.all_objects.get(institution=self.school)
        url = reverse("enterprise:sponsor_receipt_pdf", args=[payment.pk])
        with patch("enterprise.views.build_sponsor_receipt_pdf", return_value=b"%PDF") as pdf:
            self.assertEqual(self.client.get(url).status_code, 200)
            self.assertIsNone(pdf.call_args.kwargs["student_name"])
        self.grant()
        from enterprise.services import set_sharing_level

        set_sharing_level(
            self.account, 3, evidence="Donor asked for named annual receipts.",
            request=None,
        )
        self.account.refresh_from_db()
        with patch("enterprise.views.build_sponsor_receipt_pdf", return_value=b"%PDF") as pdf:
            self.assertEqual(self.client.get(url).status_code, 200)
            self.assertEqual(pdf.call_args.kwargs["student_name"], self.student.name)

    def test_a_payment_from_another_school_is_not_on_the_statement(self):
        other_school = Institution.objects.create(
            name="Remote School", slug="remote-school-sponsors", type="primary"
        )
        set_module(other_school, "sponsor_portal", True)
        with use_institution(other_school):
            outsider = StudentFactory(
                student_no="SP-OUT", programme=ProgrammeFactory(code="SPON-OUT")
            )
            invoice = Invoice.objects.create(
                student=outsider, description="Tuition", amount=Decimal("100.00"),
                due_date=date.today(),
            )
            Payment.objects.create(
                invoice=invoice, amount=Decimal("100.00"), method=Payment.Method.CARD,
                payer_name=self.sponsor.name,
                reference=f"SPONSOR-{self.sponsor.pk}-outside",
            )
        rows = sponsor_statement(self.account)["rows"]
        self.assertFalse(any(row["amount"] == Decimal("100.00") for row in rows))


class SponsorNoticeAndReportTests(SponsorBase):
    def test_notifications_only_go_to_users_who_opted_in(self):
        # Invitations in setUpTestData already used the central email pipeline.
        emails_before = OutboundMessage.objects.count()
        self.account.notify_email = False
        self.account.notify_in_app = False
        self.account.save(update_fields=["notify_email", "notify_in_app"])
        notify_sponsor(self.account, "Quiet", "No messages expected.")
        self.assertEqual(Notification.objects.count(), 0)
        self.assertEqual(OutboundMessage.objects.count(), emails_before)

        self.account.notify_email = True
        self.account.notify_in_app = True
        self.account.save(update_fields=["notify_email", "notify_in_app"])
        notify_sponsor(self.account, "Opted in", "A message is expected.")
        self.assertEqual(Notification.objects.count(), 1)
        self.assertEqual(OutboundMessage.objects.count(), emails_before + 1)

    def test_donor_report_totals_by_sponsor_without_student_names(self):
        self.make_sensitive_ready()
        self.client.force_login(self.owner)
        self.pay()
        with use_institution(self.school):
            Discount.objects.create(
                student=self.student, sponsor=self.sponsor, name="Need-based award",
                category=Discount.Category.SCHOLARSHIP, kind=Discount.Kind.FIXED,
                value=Decimal("25.00"), amount=Decimal("25.00"),
            )
        self.client.force_login(self.platform_user)
        response = self.client.get(reverse("enterprise:sponsor_donor_report"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hope Foundation")
        self.assertNotContains(response, self.student.name)
        self.assertContains(response, "200.00")
        export = self.client.get(
            reverse("enterprise:sponsor_donor_report") + "?format=csv"
        )
        self.assertNotIn(self.student.name, export.content.decode())
        self.assertIn("Hope Foundation", export.content.decode())

    def test_platform_admin_can_link_a_student_to_a_sponsor(self):
        self.client.force_login(self.platform_user)
        response = self.client.post(
            reverse("enterprise:sponsor_account_detail", args=[self.account.pk]),
            {
                "action": "link", "student": self.student.pk,
                "percentage": "25", "reference": "Top-up",
            },
        )
        self.assertEqual(response.status_code, 302)
        links = Sponsorship.all_objects.filter(
            institution=self.school, sponsor=self.sponsor, student=self.student
        )
        self.assertEqual(links.count(), 2)

    def test_audit_rows_never_carry_tokens_or_student_names(self):
        token = self.account.verification_token
        self.make_sensitive_ready()
        self.client.force_login(self.owner)
        self.pay()
        self.client.post(reverse("enterprise:sponsor_settings"), {
            "action": "notify", "notify_in_app": "on",
            "evidence": token, "reason": token,
        })
        audit = " ".join(
            str(row.after) for row in AuditEntry.objects.filter(institution=self.school)
        )
        platform = " ".join(
            str(row.detail) for row in PlatformAuditLog.objects.filter(institution=self.school)
        )
        self.assertNotIn(token, audit)
        self.assertNotIn(token, platform)
        self.assertNotIn(self.student.name, audit)
        self.assertNotIn(self.student.name, platform)
