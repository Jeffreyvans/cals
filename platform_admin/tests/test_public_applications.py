"""The public application form, uploads, status link and privacy pages."""

from __future__ import annotations

import hashlib

from django.core import mail  # noqa: F401 - the test settings use the locmem backend
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from config.brand import PRODUCT_NAME
from core.models import Institution, OutboundMessage
from platform_admin.legal import DATA_PROCESSING_VERSION
from platform_admin.models import (
    ApplicationDocument,
    ApplicationEvent,
    ApplicationMessage,
    InstitutionApplication,
)
from platform_admin.services import create_application
from platform_admin.uploads import sanitise_upload

from .base import MediaMixin, PlatformTestBase


class ApplyPageTests(PlatformTestBase):
    def test_the_apply_form_is_public_and_linked_from_the_landing_page(self):
        response = self.client.get(reverse("platform_admin:apply"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Apply to use {PRODUCT_NAME}")
        self.assertContains(response, "does not tell you whether an institution already has an account")
        self.assertContains(response, DATA_PROCESSING_VERSION)

        landing = self.client.get(reverse("landing"))
        self.assertEqual(landing.status_code, 200)
        self.assertContains(landing, reverse("platform_admin:apply"))

    def test_privacy_and_data_processing_pages_are_public(self):
        privacy = self.client.get(reverse("platform_admin:privacy"))
        self.assertEqual(privacy.status_code, 200)
        self.assertContains(privacy, "Zimbabwe Cyber and Data Protection Act")
        self.assertContains(privacy, "lawyer")

        statement = self.client.get(reverse("platform_admin:data_processing"))
        self.assertEqual(statement.status_code, 200)
        self.assertContains(statement, "data controller")
        self.assertContains(statement, "data processor")
        self.assertContains(statement, DATA_PROCESSING_VERSION)


class ApplicationSubmissionTests(MediaMixin, PlatformTestBase):
    def test_a_valid_application_gets_a_reference_email_and_status_link(self):
        response = self.client.post(
            reverse("platform_admin:apply"), self.application_payload()
        )
        self.assertEqual(response.status_code, 302)

        application = InstitutionApplication.objects.get()
        self.assertRegex(application.reference, r"^APP-\d{4}-[A-Z0-9]{6}$")
        self.assertEqual(application.status, InstitutionApplication.Status.SUBMITTED)
        self.assertEqual(application.institution_name, "Newtown Secondary School")
        self.assertEqual(application.contact_person, "Tariro Moyo")
        self.assertTrue(application.agreed_to_terms)
        self.assertEqual(application.consent_statement_version, DATA_PROCESSING_VERSION)
        self.assertIsNotNone(application.consent_accepted_at)
        self.assertIn("core_academics", application.modules)

        self.assertIn(str(application.applicant_token), response["Location"])
        self.assertTrue(
            ApplicationEvent.objects.filter(
                application=application, action="submitted"
            ).exists()
        )

        message = OutboundMessage.objects.filter(
            recipient="head@newtown.example"
        ).latest("created")
        self.assertIn(application.reference, message.subject)
        self.assertIn(application.reference, message.body)
        self.assertIn(str(application.applicant_token), message.body)
        self.assertTrue(message.html_body)
        self.assertEqual(message.status, OutboundMessage.Status.SENT)

    def test_an_application_for_an_existing_institution_does_not_reveal_it(self):
        Institution.objects.create(
            name="Newtown Secondary School", slug="newtown-existing", type="secondary"
        )
        response = self.client.post(
            reverse("platform_admin:apply"), self.application_payload()
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(InstitutionApplication.objects.count(), 1)
        application = InstitutionApplication.objects.get()
        self.assertEqual(application.status, InstitutionApplication.Status.SUBMITTED)

    def test_the_honeypot_silently_accepts_without_creating_a_row(self):
        response = self.client.post(
            reverse("platform_admin:apply"),
            self.application_payload(website_url="https://bot.example"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Thank you")
        self.assertFalse(InstitutionApplication.objects.exists())
        self.assertFalse(OutboundMessage.objects.exists())

    def test_validation_records_an_attempt_and_creates_nothing(self):
        payload = self.application_payload(email="not-an-email")
        response = self.client.post(reverse("platform_admin:apply"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(InstitutionApplication.objects.exists())
        self.assertEqual(cache.get("edusphere.platform.apply.127.0.0.1"), 1)

    def test_rate_limiting_stops_a_sixth_application_from_one_connection(self):
        for number in range(5):
            payload = self.application_payload(
                institution_name=f"School {number}",
                email=f"head{number}@example.com",
            )
            response = self.client.post(reverse("platform_admin:apply"), payload)
            self.assertEqual(response.status_code, 302)
        self.assertEqual(InstitutionApplication.objects.count(), 5)

        response = self.client.post(
            reverse("platform_admin:apply"),
            self.application_payload(institution_name="Blocked", email="blocked@example.com"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Too many applications")
        self.assertEqual(InstitutionApplication.objects.count(), 5)

    def test_a_valid_png_is_decoded_and_re_encoded(self):
        payload = self.application_payload(registration_certificate=self.png_upload())
        response = self.client.post(reverse("platform_admin:apply"), payload)
        self.assertEqual(response.status_code, 302)

        document = ApplicationDocument.objects.get()
        self.assertEqual(document.kind, ApplicationDocument.Kind.REGISTRATION_CERTIFICATE)
        self.assertEqual(document.content_type, "image/png")
        self.assertEqual(document.original_name, "certificate.png")
        self.assertTrue(document.file.name.endswith(".png"))
        self.assertEqual(len(document.sha256), 64)
        self.assertGreater(document.size, 0)

    def test_an_executable_upload_is_refused(self):
        bad = SimpleUploadedFile("malware.exe", b"MZ\x90\x00payload", content_type="application/x-msdownload")
        response = self.client.post(
            reverse("platform_admin:apply"),
            self.application_payload(authorisation_letter=bad),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Upload a PDF, PNG, JPG or WEBP file.")
        self.assertFalse(InstitutionApplication.objects.exists())

    def test_an_oversized_upload_is_refused(self):
        big = SimpleUploadedFile(
            "big.png", b"x" * (5 * 1024 * 1024 + 1), content_type="image/png"
        )
        response = self.client.post(
            reverse("platform_admin:apply"),
            self.application_payload(registration_certificate=big),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "5 MB or smaller")
        self.assertFalse(InstitutionApplication.objects.exists())

    def test_a_pdf_with_active_content_is_refused(self):
        pdf = SimpleUploadedFile(
            "certificate.pdf",
            b"%PDF-1.4\n1 0 obj\n<< /OpenAction 2 0 R >>\n%%EOF",
            content_type="application/pdf",
        )
        response = self.client.post(
            reverse("platform_admin:apply"),
            self.application_payload(registration_certificate=pdf),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "active content")
        self.assertFalse(InstitutionApplication.objects.exists())


class UploadSanitiserTests(PlatformTestBase):
    def test_a_tiny_image_is_refused(self):
        with self.assertRaises(ValidationError):
            sanitise_upload(self.png_upload(size=(50, 50)), stem="certificate")

    def test_a_valid_png_keeps_a_stable_hash_of_the_clean_bytes(self):
        safe = sanitise_upload(self.png_upload(), stem="certificate")
        self.assertEqual(safe.content_type, "image/png")
        self.assertEqual(safe.sha256, hashlib.sha256(safe.content.read()).hexdigest())
        self.assertEqual(safe.name, "certificate.png")

    def test_an_empty_upload_is_refused(self):
        with self.assertRaises(ValidationError):
            sanitise_upload(SimpleUploadedFile("empty.png", b"", content_type="image/png"))


class ApplicationStatusTests(PlatformTestBase):
    def make_application(self, **overrides):
        return create_application(
            self.application_payload(**overrides),
            request=None,
        )

    def test_the_private_status_link_shows_the_application(self):
        application = self.make_application()
        response = self.client.get(
            reverse("platform_admin:apply_status", args=[application.applicant_token])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, application.reference)
        self.assertContains(response, "Newtown Secondary School")

    def test_an_unknown_status_token_is_not_found(self):
        response = self.client.get(
            reverse(
                "platform_admin:apply_status",
                args=["00000000-0000-0000-0000-000000000000"],
            )
        )
        self.assertEqual(response.status_code, 404)

    def test_an_applicant_can_reply_once_more_information_is_requested(self):
        application = self.make_application()
        application.status = InstitutionApplication.Status.MORE_INFORMATION_NEEDED
        application.save(update_fields=["status"])

        response = self.client.post(
            reverse("platform_admin:apply_reply", args=[application.applicant_token]),
            {"message": "Here is the registration certificate you asked for."},
        )
        self.assertEqual(response.status_code, 302)
        application.refresh_from_db()
        self.assertEqual(application.status, InstitutionApplication.Status.UNDER_REVIEW)
        self.assertTrue(
            ApplicationMessage.objects.filter(
                application=application,
                author_kind=ApplicationMessage.AuthorKind.APPLICANT,
            ).exists()
        )

    def test_a_reply_is_refused_when_nothing_was_asked_for(self):
        application = self.make_application()
        response = self.client.post(
            reverse("platform_admin:apply_reply", args=[application.applicant_token]),
            {"message": "An unsolicited reply."},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ApplicationMessage.objects.exists())

    def test_an_applicant_can_withdraw_an_open_application(self):
        application = self.make_application()
        response = self.client.post(
            reverse("platform_admin:apply_withdraw", args=[application.applicant_token])
        )
        self.assertEqual(response.status_code, 302)
        application.refresh_from_db()
        self.assertEqual(application.status, InstitutionApplication.Status.WITHDRAWN)
        self.assertTrue(
            ApplicationEvent.objects.filter(
                application=application, action="withdrawn"
            ).exists()
        )

    def test_a_withdrawn_application_cannot_be_withdrawn_again(self):
        application = self.make_application()
        application.status = InstitutionApplication.Status.WITHDRAWN
        application.save(update_fields=["status"])
        response = self.client.post(
            reverse("platform_admin:apply_withdraw", args=[application.applicant_token])
        )
        self.assertEqual(response.status_code, 302)
        application.refresh_from_db()
        self.assertEqual(application.status, InstitutionApplication.Status.WITHDRAWN)
