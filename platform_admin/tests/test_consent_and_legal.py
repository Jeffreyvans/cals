"""Data processing consent: the statement, the record and the institution views.

The platform is the processor and the institution is the controller. These tests
cover the public explanation, the consent record written when an application is
approved, and the institution's own ability to see and download that record.
"""

from __future__ import annotations

import hashlib

from django.urls import reverse

from platform_admin.legal import (
    DATA_PROCESSING_STATEMENT,
    DATA_PROCESSING_VERSION,
    PLATFORM_PRIVACY_NOTICE,
)
from platform_admin.models import DataConsentLog
from platform_admin.services import approve_application, create_application

from .base import PlatformTestBase


class PublicLegalPageTests(PlatformTestBase):
    def test_the_data_processing_page_names_both_roles(self):
        response = self.client.get(reverse("platform_admin:data_processing"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, DATA_PROCESSING_VERSION)
        self.assertContains(response, "data controller")
        self.assertContains(response, "data processor")
        self.assertContains(response, "Zimbabwe Cyber and Data Protection Act")
        self.assertContains(response, "lawyer")

    def test_the_privacy_page_says_what_platform_staff_may_not_do(self):
        response = self.client.get(reverse("platform_admin:privacy"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Platform privacy notice")
        self.assertContains(response, "We do not sell personal data")
        self.assertContains(response, "recorded consent")
        self.assertContains(response, "six years")

    def test_the_apply_form_shows_the_statement_being_accepted(self):
        response = self.client.get(reverse("platform_admin:apply"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, DATA_PROCESSING_VERSION)
        self.assertContains(response, "data controller")

    def test_the_statement_itself_keeps_the_plain_language_promise(self):
        self.assertIn("The institution is the data controller", DATA_PROCESSING_STATEMENT)
        self.assertIn("data processor", DATA_PROCESSING_STATEMENT)
        self.assertIn("approve, narrow, refuse or revoke", DATA_PROCESSING_STATEMENT)
        self.assertIn("two hours", DATA_PROCESSING_STATEMENT)
        self.assertIn("A lawyer should review", DATA_PROCESSING_STATEMENT)
        self.assertIn("Zimbabwe Cyber and Data Protection Act", PLATFORM_PRIVACY_NOTICE)


class ApprovalConsentLogTests(PlatformTestBase):
    def setUp(self):
        super().setUp()
        # The fixtures build bare institutions, so create the default roles that
        # give the administrator the settings permission the real product seeds.
        from core.roles import ensure_roles

        ensure_roles(self.inst)

    def test_approving_an_application_records_the_consent(self):
        application = create_application(
            self.application_payload(agreed_to_terms=True)
        )
        institution = approve_application(application, self.main)

        log = DataConsentLog.objects.get(institution=institution)
        self.assertEqual(log.application, application)
        self.assertEqual(log.kind, DataConsentLog.Kind.DATA_PROCESSING)
        self.assertEqual(log.version, DATA_PROCESSING_VERSION)
        self.assertEqual(log.statement_text, DATA_PROCESSING_STATEMENT)
        self.assertEqual(
            log.statement_hash,
            hashlib.sha256(DATA_PROCESSING_STATEMENT.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(log.accepted_by_name, application.contact_person)
        self.assertEqual(log.accepted_by_email, application.email)
        self.assertIsNotNone(log.accepted_at)

        institution.refresh_from_db()
        self.assertIsNotNone(institution.data_processing_accepted_at)
        self.assertEqual(institution.data_processing_version, DATA_PROCESSING_VERSION)

    def test_the_application_keeps_the_version_the_applicant_saw(self):
        application = create_application(
            self.application_payload(agreed_to_terms=True)
        )
        self.assertEqual(application.consent_statement_version, DATA_PROCESSING_VERSION)
        self.assertIsNotNone(application.consent_accepted_at)

    def test_an_institution_can_accept_the_statement_from_its_own_settings(self):
        self.login(self.inst_admin)
        response = self.client.post(
            reverse("platform_admin:institution_accept_data_processing"),
            {"accepted": "on", "confirmed_by": "Tariro Moyo"},
        )
        self.assertEqual(response.status_code, 302)

        log = DataConsentLog.objects.get(institution=self.inst)
        self.assertEqual(log.accepted_by_name, "Tariro Moyo")
        self.assertEqual(log.accepted_by, self.inst_admin)
        self.assertEqual(log.accepted_by_email, self.inst_admin.email)
        self.assertEqual(
            log.statement_hash,
            hashlib.sha256(DATA_PROCESSING_STATEMENT.encode("utf-8")).hexdigest(),
        )
        self.inst.refresh_from_db()
        self.assertEqual(self.inst.data_processing_version, DATA_PROCESSING_VERSION)

    def test_the_institution_settings_page_shows_the_links_and_the_status(self):
        self.login(self.inst_admin)
        response = self.client.get(reverse("institution_settings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Data protection and platform access")
        self.assertContains(response, "data controller")
        self.assertContains(response, "Consent requests")
        self.assertContains(response, "Who accessed our data")
        self.assertContains(response, "Data processing consent")
        self.assertContains(response, "not recorded yet")

    def test_the_settings_page_shows_the_accepted_version_after_acceptance(self):
        self.login(self.inst_admin)
        self.client.post(
            reverse("platform_admin:institution_accept_data_processing"),
            {"accepted": "on", "confirmed_by": "Tariro Moyo"},
        )
        response = self.client.get(reverse("institution_settings"))
        self.assertContains(response, f"version {DATA_PROCESSING_VERSION}")

    def test_the_consent_log_can_be_read_and_downloaded_by_the_institution(self):
        self.login(self.inst_admin)
        self.client.post(
            reverse("platform_admin:institution_accept_data_processing"),
            {"accepted": "on", "confirmed_by": "Tariro Moyo"},
        )
        log = DataConsentLog.objects.get(institution=self.inst)

        response = self.client.get(reverse("platform_admin:institution_consent_log"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Data processing consent")
        self.assertContains(response, "Tariro Moyo")
        self.assertContains(response, DATA_PROCESSING_VERSION)

        response = self.client.get(
            reverse("platform_admin:institution_consent_log") + "?export=csv"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("data-consent-log.csv", response["Content-Disposition"])
        self.assertIn(log.statement_hash.encode(), response.content)

    def test_a_platform_admin_cannot_accept_on_behalf_of_an_institution(self):
        self.login(self.main)
        response = self.client.post(
            reverse("platform_admin:institution_accept_data_processing"),
            {"accepted": "on", "confirmed_by": "Platform Main"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(DataConsentLog.objects.exists())

    def test_one_institution_cannot_read_another_institutions_consent_log(self):
        self.client.post(
            reverse("platform_admin:institution_accept_data_processing"),
            {"accepted": "on", "confirmed_by": "Alpha Admin"},
        )
        self.login(self.other_admin)
        response = self.client.get(reverse("platform_admin:institution_consent_log"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Alpha Admin")
        self.assertContains(response, "No consent record yet")

    def test_accepting_again_adds_a_new_row_instead_of_overwriting(self):
        self.login(self.inst_admin)
        for name in ("First Person", "Second Person"):
            self.client.post(
                reverse("platform_admin:institution_accept_data_processing"),
                {"accepted": "on", "confirmed_by": name},
            )
        logs = DataConsentLog.objects.filter(institution=self.inst)
        self.assertEqual(logs.count(), 2)
        self.assertEqual(
            set(logs.values_list("accepted_by_name", flat=True)),
            {"First Person", "Second Person"},
        )
