"""Part 3: custom domains, white-label attribution and reseller isolation."""

from __future__ import annotations

import re
from decimal import Decimal
from email.utils import parseaddr
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.http import Http404
from django.template.loader import render_to_string
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from config.brand import COMPANY_NAME, POWERED_BY, PRODUCT_NAME
from core.cache import institution_by_domain
from core.hosts import refresh_dynamic_hosts, verified_domains
from core.models import AuditEntry, Institution
from core.modules import set_module
from core.notifications import _from_email
from core.roles import ensure_roles
from enterprise.access import (
    ResellerRole,
    active_reseller_accounts_for,
    feature_enabled,
)
from enterprise.forms import ResellerAccountForm
from enterprise.models import ResellerAccount, ResellerCommission
from enterprise.services import (
    accrue_reseller_commissions,
    attach_institution,
    create_organisation,
    create_reseller_account,
    mark_commission_paid,
    reseller_portal_data,
)
from platform_admin.forms import InstitutionDomainForm
from platform_admin.models import (
    Plan,
    PlatformAuditLog,
    PlatformInvoice,
    PlatformPayment,
    PlatformStaff,
    Subscription,
)
from platform_admin.services import ensure_platform_roles

User = get_user_model()
PASSWORD = "Str0ngPass!2026"


def detail_payload(**overrides):
    data = {
        "action": "edit",
        "name": "Alpha Campus",
        "slug": "alpha-wl",
        "type": "secondary",
        "address": "",
        "contact_email": "office@alpha.example",
        "contact_phone": "",
        "website": "",
        "access_grant_max_hours": 24,
        "public_page_enabled": "on",
    }
    data.update(overrides)
    return data


class WhiteLabelBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alpha = Institution.objects.create(
            name="Alpha Campus", slug="alpha-wl", type="secondary",
            contact_email="office@alpha.example",
        )
        cls.beta = Institution.objects.create(
            name="Beta Campus", slug="beta-wl", type="college"
        )
        cls.outside = Institution.objects.create(
            name="Outside Campus", slug="outside-wl", type="secondary"
        )
        for institution in (cls.alpha, cls.beta, cls.outside):
            set_module(institution, "white_label", True)
            set_module(institution, "reseller", True)
            ensure_roles(institution)

        cls.alpha.custom_domain = "learn.alpha.example"
        cls.alpha.custom_domain_verified = True
        cls.alpha.custom_domain_enabled = True
        cls.alpha.custom_domain_token = "alpha-token"
        cls.alpha.save()
        cls.beta.custom_domain = "learn.beta.example"
        cls.beta.custom_domain_verified = True
        cls.beta.custom_domain_enabled = True
        cls.beta.custom_domain_token = "beta-token"
        cls.beta.save()

        cls.reseller_org = create_organisation(
            name="Alpha Partners", slug="alpha-partners", org_type="reseller"
        )
        attach_institution(cls.reseller_org, cls.alpha, campus_label="Alpha Campus")
        attach_institution(cls.reseller_org, cls.beta, campus_label="Beta Campus")
        cls.other_org = create_organisation(
            name="Other Partners", slug="other-partners", org_type="reseller"
        )
        attach_institution(cls.other_org, cls.outside, campus_label="Outside Campus")

        cls.reseller_admin = User.objects.create_user(
            "reselleradmin", password=PASSWORD, role="staff", institution=cls.alpha
        )
        cls.reseller_viewer = User.objects.create_user(
            "resellerviewer", password=PASSWORD, role="staff", institution=cls.alpha
        )
        cls.other_admin = User.objects.create_user(
            "otherreseller", password=PASSWORD, role="staff", institution=cls.outside
        )
        cls.platform_main = User.objects.create_user(
            "wlplatformmain", password=PASSWORD, role="staff", institution=cls.alpha
        )
        cls.platform_billing = User.objects.create_user(
            "wlplatformbilling", password=PASSWORD, role="staff", institution=cls.alpha
        )
        roles = ensure_platform_roles()
        PlatformStaff.objects.create(
            user=cls.platform_main, role=roles["main_admin"], is_active=True
        )
        PlatformStaff.objects.create(
            user=cls.platform_billing, role=roles["billing"], is_active=True
        )

        cls.account = create_reseller_account(
            user=cls.reseller_admin, organisation=cls.reseller_org,
            role=ResellerRole.ADMIN, actor=cls.platform_main,
        )
        cls.viewer_account = create_reseller_account(
            user=cls.reseller_viewer, organisation=cls.reseller_org,
            role=ResellerRole.VIEWER, actor=cls.platform_main,
        )
        cls.other_account = create_reseller_account(
            user=cls.other_admin, organisation=cls.other_org,
            role=ResellerRole.ADMIN, actor=cls.platform_main,
        )

    def setUp(self):
        cache.clear()

    def invoice(self, amount="200.00", institution=None):
        return PlatformInvoice.objects.create(
            institution=institution or self.alpha,
            number=f"WL-INV-{PlatformInvoice.objects.count() + 1}",
            amount=Decimal(amount), currency="USD",
            status=PlatformInvoice.Status.SENT,
        )


class CustomDomainFeatureTests(WhiteLabelBase):
    def test_a_verified_domain_only_resolves_while_white_label_is_enabled(self):
        self.assertEqual(
            institution_by_domain("learn.alpha.example").pk, self.alpha.pk
        )
        self.assertIn("learn.alpha.example", verified_domains())
        set_module(self.alpha, "white_label", False)
        self.assertIsNone(institution_by_domain("learn.alpha.example"))
        self.assertNotIn("learn.alpha.example", verified_domains())
        response = self.client.get("/login/", HTTP_HOST="learn.alpha.example")
        self.assertIsNone(response.wsgi_request.institution)

    def test_a_cancelled_plan_disables_domains_and_reseller_access_at_once(self):
        plan = Plan.objects.create(
            code="enterprise-wl", name="Enterprise (test)",
            monthly_price="199.00", modules=["white_label", "reseller"],
        )
        for institution in (self.alpha, self.beta):
            Subscription.objects.create(
                institution=institution, plan=plan, status=Subscription.Status.CANCELLED
            )
        self.assertFalse(feature_enabled("white_label", self.alpha))
        self.assertFalse(feature_enabled("reseller", self.alpha))
        self.assertEqual(verified_domains(), [])
        with self.assertRaises(Http404):
            active_reseller_accounts_for(self.reseller_admin)
        self.client.force_login(self.reseller_admin)
        response = self.client.get(
            reverse("enterprise:reseller_dashboard", args=[self.reseller_org.slug])
        )
        self.assertEqual(response.status_code, 404)

    def test_duplicate_domains_and_invalid_hostnames_are_refused(self):
        duplicate = InstitutionDomainForm(
            {"custom_domain": "LEARN.BETA.EXAMPLE."}, institution=self.alpha
        )
        self.assertFalse(duplicate.is_valid())
        self.assertIn("already configured", str(duplicate.errors))
        invalid = InstitutionDomainForm(
            {"custom_domain": "not a domain"}, institution=self.alpha
        )
        self.assertFalse(invalid.is_valid())

    def test_removing_a_domain_clears_verification_without_touching_the_campus(self):
        self.client.force_login(self.platform_main)
        url = reverse("platform_admin:institution_domain", args=[self.alpha.pk])
        response = self.client.post(url, {"action": "remove"})
        self.assertEqual(response.status_code, 302)
        self.alpha.refresh_from_db()
        self.assertEqual(self.alpha.custom_domain, "")
        self.assertEqual(self.alpha.custom_domain_token, "")
        self.assertFalse(self.alpha.custom_domain_verified)
        self.assertFalse(self.alpha.custom_domain_enabled)
        self.assertTrue(Institution.objects.filter(pk=self.alpha.pk).exists())
        self.assertTrue(
            PlatformAuditLog.objects.filter(
                action="institution.custom_domain_removed"
            ).exists()
        )
        cache.clear()
        self.assertIsNone(institution_by_domain("learn.alpha.example"))

    def test_the_domain_panel_and_branding_switch_need_the_feature(self):
        set_module(self.alpha, "white_label", False)
        self.client.force_login(self.platform_main)
        self.assertEqual(
            self.client.get(
                reverse("platform_admin:institution_domain", args=[self.alpha.pk])
            ).status_code,
            404,
        )
        page = self.client.get(
            reverse("platform_admin:institution_detail", args=[self.alpha.pk])
        )
        self.assertNotContains(page, 'name="show_platform_branding"')
        self.assertContains(page, "needs the <code>white_label</code> plan feature")
        response = self.client.post(
            reverse("platform_admin:institution_detail", args=[self.alpha.pk]),
            detail_payload(show_platform_branding=""),
        )
        self.assertEqual(response.status_code, 302)
        self.alpha.refresh_from_db()
        self.assertTrue(self.alpha.show_platform_branding)

    def test_a_dns_check_never_bypasses_explicit_confirmation(self):
        self.client.force_login(self.platform_main)
        url = reverse("platform_admin:institution_domain", args=[self.alpha.pk])
        self.alpha.custom_domain_verified = False
        self.alpha.custom_domain_enabled = False
        self.alpha.save(update_fields=["custom_domain_verified", "custom_domain_enabled"])
        with patch("core.domains.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("1.2.3.4", 443))]):
            response = self.client.post(url, {"action": "check"})
        self.assertEqual(response.status_code, 302)
        self.client.post(url, {"action": "verify"})
        self.alpha.refresh_from_db()
        self.assertFalse(self.alpha.custom_domain_enabled)


class DynamicHostTests(WhiteLabelBase):
    def test_verified_hosts_are_added_without_removing_environment_hosts(self):
        with patch("core.hosts._applied_hosts", set()), \
                patch("core.hosts._applied_origins", set()), \
                override_settings(
                    ALLOWED_HOSTS=["vanpere.test"],
                    CSRF_TRUSTED_ORIGINS=["https://vanpere.test"],
                ):
            refresh_dynamic_hosts()
            self.assertIn("vanpere.test", settings.ALLOWED_HOSTS)
            self.assertIn("learn.alpha.example", settings.ALLOWED_HOSTS)
            self.assertIn("learn.beta.example", settings.ALLOWED_HOSTS)
            self.assertIn("https://vanpere.test", settings.CSRF_TRUSTED_ORIGINS)
            self.assertIn("https://learn.alpha.example", settings.CSRF_TRUSTED_ORIGINS)

            self.beta.custom_domain_enabled = False
            self.beta.save(update_fields=["custom_domain_enabled"])
            refresh_dynamic_hosts()
            self.assertNotIn("learn.beta.example", settings.ALLOWED_HOSTS)
            self.assertNotIn("https://learn.beta.example", settings.CSRF_TRUSTED_ORIGINS)
            self.assertIn("vanpere.test", settings.ALLOWED_HOSTS)
            self.assertIn("learn.alpha.example", settings.ALLOWED_HOSTS)

    def test_a_verified_host_serves_a_page_and_a_cross_domain_post(self):
        with patch("core.hosts._applied_hosts", set()), \
                patch("core.hosts._applied_origins", set()), \
                override_settings(
                    ALLOWED_HOSTS=["vanpere.test"],
                    CSRF_TRUSTED_ORIGINS=["https://vanpere.test"],
                ):
            host = "learn.alpha.example"
            client = Client(enforce_csrf_checks=True)
            page = client.get(reverse("login"), HTTP_HOST=host, secure=True)
            self.assertEqual(page.status_code, 200)
            self.assertIn(host, settings.ALLOWED_HOSTS)
            self.assertIn(f"https://{host}", settings.CSRF_TRUSTED_ORIGINS)
            match = re.search(
                r'name="csrfmiddlewaretoken"\s+value="([^"]+)"',
                page.content.decode(),
            )
            self.assertIsNotNone(match)
            response = client.post(reverse("login"), {
                "username": self.reseller_admin.username,
                "password": PASSWORD,
                "csrfmiddlewaretoken": match.group(1),
            }, HTTP_HOST=host, HTTP_ORIGIN=f"https://{host}", secure=True)
            self.assertEqual(response.status_code, 302)

    def test_a_session_cannot_follow_a_user_to_another_host(self):
        self.client.force_login(self.reseller_admin)
        # force_login happens on the default test host; bind this session to
        # the custom host as a real sign-in there would.
        session = self.client.session
        session["edusphere_bound_host"] = "learn.alpha.example"
        session.save()
        self.client.get("/dashboard/", HTTP_HOST="learn.alpha.example")
        session = self.client.session
        session["reseller_account_id"] = self.account.pk
        session.save()

        response = self.client.get(
            reverse("enterprise:reseller_dashboard", args=[self.reseller_org.slug]),
            HTTP_HOST="learn.beta.example",
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(
            reverse("enterprise:reseller_login", args=[self.reseller_org.slug]),
            response["Location"],
        )
        self.assertNotIn("reseller_account_id", self.client.session)
        self.assertIsNone(self.client.session.get("edusphere_bound_host"))


class WhiteLabelBrandingTests(WhiteLabelBase):
    def test_suppressing_the_credit_keeps_legal_identity_and_sender_address(self):
        self.alpha.show_platform_branding = False
        self.alpha.save(update_fields=["show_platform_branding"])
        html = render_to_string(
            "emails/generic.html",
            {"subject": "Hello", "body_html": "<p>Plain body</p>",
             "institution": self.alpha},
        )
        self.assertNotIn(POWERED_BY, html)
        self.assertNotIn(f"via {PRODUCT_NAME}", html)
        self.assertIn("Alpha Campus", html)
        self.assertIn(COMPANY_NAME, html)
        self.assertIn(str(timezone.localdate().year), html)
        self.assertEqual(parseaddr(_from_email(self.alpha))[1],
                         parseaddr(settings.DEFAULT_FROM_EMAIL)[1])
        self.assertEqual(parseaddr(_from_email(self.alpha))[0], "Alpha Campus")


class ResellerPortalTests(WhiteLabelBase):
    def test_a_reseller_sees_only_its_own_clients_and_commissions(self):
        self.client.force_login(self.reseller_admin)
        url = reverse("enterprise:reseller_dashboard", args=[self.reseller_org.slug])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alpha Campus")
        self.assertContains(response, "Beta Campus")
        self.assertNotContains(response, "Outside Campus")

        invoice = self.invoice()
        payment = PlatformPayment.objects.create(
            invoice=invoice, amount=Decimal("100.00"), recorded_by=self.platform_billing
        )
        accrue_reseller_commissions(invoice, payment)
        data = reseller_portal_data(self.account)
        self.assertEqual(data["totals"]["accrued"], Decimal("10.00"))

        filtered = self.client.get(f"{url}?client={self.outside.pk}")
        self.assertEqual(filtered.status_code, 200)
        # An out-of-scope client id cannot expand the rows or commissions: it
        # matches none of this reseller's campuses, so the data tables are empty.
        self.assertEqual(filtered.context["data"]["rows"], [])
        self.assertEqual(filtered.context["data"]["commissions"], [])
        self.assertContains(filtered, "No enabled clients to show.")
        self.assertContains(filtered, "No commission rows yet.")

    def test_another_reseller_cannot_open_this_portal(self):
        self.client.force_login(self.other_admin)
        response = self.client.get(
            reverse("enterprise:reseller_dashboard", args=[self.reseller_org.slug])
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("Alpha Partners", response.content.decode())

    def test_commission_accrues_once_per_existing_payment(self):
        invoice = self.invoice("200.00")
        payment = PlatformPayment.objects.create(
            invoice=invoice, amount=Decimal("100.00"), recorded_by=self.platform_billing
        )
        rows = accrue_reseller_commissions(invoice, payment)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].amount, Decimal("10.00"))
        self.assertEqual(rows[0].account, self.account)
        self.assertEqual(accrue_reseller_commissions(invoice, payment), [rows[0]])
        self.assertEqual(ResellerCommission.objects.filter(payment=payment).count(), 1)
        self.assertTrue(
            AuditEntry.objects.filter(action="reseller.commission_accrued").exists()
        )

        other_invoice = self.invoice("100.00", institution=self.outside)
        other_payment = PlatformPayment.objects.create(
            invoice=other_invoice, amount=Decimal("80.00")
        )
        other_rows = accrue_reseller_commissions(other_invoice, other_payment)
        self.assertEqual(len(other_rows), 1)
        self.assertEqual(other_rows[0].account, self.other_account)
        self.assertEqual(ResellerCommission.objects.count(), 2)
        self.assertFalse(
            ResellerCommission.objects.filter(
                account=self.account, institution=self.outside
            ).exists()
        )

    def test_recording_a_platform_payment_accrues_commission(self):
        invoice = self.invoice("200.00")
        self.client.force_login(self.platform_billing)
        response = self.client.post(
            reverse("platform_admin:invoice_detail", args=[invoice.pk]),
            {
                "invoice": invoice.pk,
                "amount": "100.00",
                "method": PlatformPayment.Method.BANK,
                "reference": "PARTNER-1",
                "paid_at": "2026-01-20T10:30",
            },
        )
        self.assertEqual(response.status_code, 302)
        commission = ResellerCommission.objects.get(platform_invoice=invoice)
        self.assertEqual(commission.amount, Decimal("10.00"))
        self.assertEqual(commission.status, ResellerCommission.Status.ACCRUED)

        self.client.force_login(self.platform_main)
        self.client.post(
            reverse("enterprise:reseller_commission_update", args=[commission.pk]),
            {"action": "pay"},
        )
        commission.refresh_from_db()
        self.assertEqual(commission.status, ResellerCommission.Status.PAID)
        self.assertIsNotNone(commission.paid_at)

    def test_mark_commission_paid_is_idempotent(self):
        invoice = self.invoice("200.00")
        payment = PlatformPayment.objects.create(
            invoice=invoice, amount=Decimal("100.00")
        )
        commission = accrue_reseller_commissions(invoice, payment)[0]
        first = mark_commission_paid(commission, actor=self.platform_main)
        second = mark_commission_paid(commission, actor=self.platform_main)
        self.assertEqual(first.paid_at, second.paid_at)
        self.assertEqual(
            AuditEntry.objects.filter(action="reseller.commission_paid").count(), 1
        )


class ResellerAccountRuleTests(WhiteLabelBase):
    def test_a_reseller_account_must_link_to_a_reseller_organisation(self):
        trust = create_organisation(
            name="Alpha Trust", slug="alpha-trust", org_type="trust"
        )
        form = ResellerAccountForm({
            "user": self.reseller_admin.pk,
            "organisation": trust.pk,
            "role": ResellerRole.ADMIN,
            "commission_rate": "10.00",
        })
        self.assertFalse(form.is_valid())
        with self.assertRaisesMessage(ValidationError, "Reseller"):
            create_reseller_account(
                user=self.reseller_admin, organisation=trust, role=ResellerRole.ADMIN
            )
        self.assertFalse(ResellerAccount.objects.filter(organisation=trust).exists())

    def test_slug_validation_protects_the_group_and_reseller_namespaces(self):
        from core.slugs import validate_slug

        with self.assertRaisesMessage(ValidationError, "already taken"):
            validate_slug("alpha-partners")
        with self.assertRaisesMessage(ValidationError, "already taken"):
            create_organisation(
                name="Another Alpha Partners", slug="alpha-partners", org_type="trust"
            )
        for reserved in ("g", "reseller", "sponsor", "platform"):
            with self.subTest(reserved=reserved):
                with self.assertRaisesMessage(ValidationError, "reserved"):
                    validate_slug(reserved)
