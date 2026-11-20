"""The platform applications inbox, review workflow and approval rules."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserInvitation
from accounts.user_admin import accept_invitation
from config.brand import PRODUCT_NAME
from core.models import Institution, InstitutionModule, NumberFormat, OutboundMessage
from platform_admin.models import (
    ApplicationDocument,
    ApplicationEvent,
    ApplicationMessage,
    DataConsentLog,
    InstitutionApplication,
    PlatformRequest,
    PlatformRole,
    PlatformStaff,
    Subscription,
)
from platform_admin.services import (
    add_internal_note,
    approve_application,
    reject_application,
    request_more_information,
)

from .base import STRONG_PASSWORD, MediaMixin, PlatformTestBase


class ApplicationAccessTests(PlatformTestBase):
    def test_anonymous_users_are_sent_to_sign_in(self):
        response = self.client.get(reverse("platform_admin:applications"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_an_institution_user_without_a_platform_row_is_refused(self):
        self.client.force_login(self.teacher)
        response = self.client.get(reverse("platform_admin:applications"))
        self.assertEqual(response.status_code, 404)

    def test_a_superuser_without_a_platform_row_is_refused(self):
        superuser = self.inst_admin.__class__.objects.create_user(
            "superbutnotplatform",
            password=STRONG_PASSWORD,
            role="admin",
            institution=self.inst,
            is_superuser=True,
            is_staff=True,
        )
        self.client.force_login(superuser)
        response = self.client.get(reverse("platform_admin:applications"))
        self.assertEqual(response.status_code, 404)

    def test_the_billing_role_cannot_open_applications(self):
        self.login(self.billing)
        response = self.client.get(reverse("platform_admin:applications"))
        self.assertEqual(response.status_code, 403)

    def test_a_read_only_viewer_can_read_but_not_decide(self):
        viewer_role = PlatformRole.objects.create(
            code="application_viewer",
            name="Application viewer",
            permissions=["platform.view_applications", "platform.view_inbox"],
        )
        viewer = self.inst_admin.__class__.objects.create_user(
            "viewerplatform",
            email="viewer@edusphere.example",
            password=STRONG_PASSWORD,
            role="staff",
            institution=self.inst,
        )
        PlatformStaff.objects.create(user=viewer, role=viewer_role)
        application = InstitutionApplication.objects.create(
            reference="APP-2026-VIEW01",
            institution_name="Viewer School",
            institution_type="secondary",
            town="Harare",
            contact_person="A Person",
            phone="+263771111111",
            email="viewer-school@example.com",
            agreed_to_terms=True,
        )

        self.client.force_login(viewer)
        response = self.client.get(
            reverse("platform_admin:application_detail", args=[application.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Viewer School")

        response = self.client.post(
            reverse("platform_admin:application_detail", args=[application.pk]),
            {"action": "reject", "reason": "This reason is long enough."},
        )
        self.assertEqual(response.status_code, 403)
        application.refresh_from_db()
        self.assertEqual(application.status, InstitutionApplication.Status.SUBMITTED)


class ApplicationListTests(PlatformTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.app_one = InstitutionApplication.objects.create(
            reference="APP-2026-AAA111",
            institution_name="Harare High",
            institution_type="secondary",
            town="Harare",
            contact_person="One Person",
            phone="+263771000001",
            email="one@example.com",
            status=InstitutionApplication.Status.SUBMITTED,
            preferred_plan=cls.plans["starter"],
            agreed_to_terms=True,
        )
        cls.app_two = InstitutionApplication.objects.create(
            reference="APP-2026-BBB222",
            institution_name="Bulawayo College",
            institution_type="college",
            town="Bulawayo",
            contact_person="Two Person",
            phone="+263771000002",
            email="two@example.com",
            status=InstitutionApplication.Status.UNDER_REVIEW,
            preferred_plan=cls.plans["standard"],
            agreed_to_terms=True,
        )

    def test_the_list_shows_both_applications(self):
        self.login(self.main)
        response = self.client.get(reverse("platform_admin:applications"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Harare High")
        self.assertContains(response, "Bulawayo College")

    def test_filters_by_status_search_and_plan(self):
        self.login(self.main)

        response = self.client.get(
            reverse("platform_admin:applications"), {"status": "submitted"}
        )
        self.assertContains(response, "Harare High")
        self.assertNotContains(response, "Bulawayo College")

        response = self.client.get(
            reverse("platform_admin:applications"), {"q": "Bulawayo"}
        )
        self.assertContains(response, "Bulawayo College")
        self.assertNotContains(response, "Harare High")

        response = self.client.get(
            reverse("platform_admin:applications"),
            {"plan": self.plans["starter"].pk},
        )
        self.assertContains(response, "Harare High")
        self.assertNotContains(response, "Bulawayo College")

    def test_the_inbox_aggregates_applications_platform_requests_and_access_requests(self):
        PlatformRequest.objects.create(
            kind=PlatformRequest.Kind.STAFF_REGISTRATION,
            title="Escalated staff registration for a new teacher",
            institution=self.inst,
            status=PlatformRequest.Status.OPEN,
        )
        access_request, _grant = self.make_request(
            categories=["personal_data"], reason="Support ticket needs investigation."
        )
        self.login(self.main)
        response = self.client.get(reverse("platform_admin:inbox"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Escalated staff registration")
        self.assertContains(response, f"AR-{access_request.pk}")
        self.assertEqual(response.context["counts"]["requests"], 1)
        self.assertEqual(response.context["counts"]["access"], 1)


class ApplicationReviewTests(MediaMixin, PlatformTestBase):
    def make_application(self, **overrides):
        data = {
            "reference": "APP-2026-REVIEW",
            "institution_name": "Review School",
            "institution_type": "secondary",
            "town": "Gweru",
            "contact_person": "Rudo Ncube",
            "position": "Head",
            "phone": "+263772222222",
            "email": "rudo@example.com",
            "website": "https://review.example",
            "preferred_plan": self.plans["starter"],
            "modules": ["core_academics", "finance"],
            "agreed_to_terms": True,
            "consent_statement_version": "1.0",
            "consent_accepted_at": timezone.now(),
        }
        data.update(overrides)
        return InstitutionApplication.objects.create(**data)

    def test_assigning_a_reviewer_moves_the_application_under_review(self):
        application = self.make_application()
        self.login(self.main)
        response = self.client.post(
            reverse("platform_admin:application_detail", args=[application.pk]),
            {"action": "assign", "reviewer": self.support.pk},
        )
        self.assertEqual(response.status_code, 302)
        application.refresh_from_db()
        self.assertEqual(application.assigned_to, self.support)
        self.assertEqual(application.status, InstitutionApplication.Status.UNDER_REVIEW)

    def test_internal_notes_are_saved_and_audited(self):
        application = self.make_application()
        self.login(self.main)
        response = self.client.post(
            reverse("platform_admin:application_detail", args=[application.pk]),
            {"action": "note", "note": "Called the head teacher and confirmed the address."},
        )
        self.assertEqual(response.status_code, 302)
        application.refresh_from_db()
        self.assertIn("Called the head teacher", application.internal_notes)
        self.assertTrue(
            ApplicationEvent.objects.filter(
                application=application, action="internal_note"
            ).exists()
        )

    def test_an_empty_internal_note_is_refused(self):
        application = self.make_application()
        with self.assertRaises(ValidationError):
            add_internal_note(application, self.main, "   ")

    def test_requesting_more_information_emails_a_secure_reply_link(self):
        application = self.make_application()
        self.login(self.main)
        response = self.client.post(
            reverse("platform_admin:application_detail", args=[application.pk]),
            {"action": "more_information", "message": "Please send the registration certificate."},
        )
        self.assertEqual(response.status_code, 302)
        application.refresh_from_db()
        self.assertEqual(
            application.status, InstitutionApplication.Status.MORE_INFORMATION_NEEDED
        )
        message = OutboundMessage.objects.filter(recipient="rudo@example.com").latest("created")
        self.assertIn(str(application.applicant_token), message.body)
        self.assertIn("registration certificate", message.body)
        self.assertTrue(
            ApplicationMessage.objects.filter(
                application=application, author_kind=ApplicationMessage.AuthorKind.STAFF
            ).exists()
        )

    def test_a_short_more_information_message_is_refused(self):
        application = self.make_application()
        with self.assertRaises(ValidationError):
            request_more_information(application, self.main, "No")

    def test_a_document_can_be_viewed_and_the_view_is_audited(self):
        application = self.make_application()
        document = ApplicationDocument.objects.create(
            application=application,
            kind=ApplicationDocument.Kind.REGISTRATION_CERTIFICATE,
            file=self.png_upload(),
            original_name="certificate.png",
            content_type="image/png",
            size=10,
        )
        self.login(self.main)
        response = self.client.get(
            reverse(
                "platform_admin:application_document",
                args=[application.pk, document.pk],
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        from platform_admin.models import PlatformAuditLog

        self.assertTrue(
            PlatformAuditLog.objects.filter(
                action="application.document_viewed",
                record_id=str(document.pk),
            ).exists()
        )

    def test_rejection_requires_a_reason_and_emails_the_applicant(self):
        application = self.make_application()
        with self.assertRaises(ValidationError):
            reject_application(application, self.main, "No")

        self.login(self.main)
        response = self.client.post(
            reverse("platform_admin:application_detail", args=[application.pk]),
            {"action": "reject", "reason": "We do not serve this region yet."},
        )
        self.assertEqual(response.status_code, 302)
        application.refresh_from_db()
        self.assertEqual(application.status, InstitutionApplication.Status.REJECTED)
        self.assertEqual(application.decision_reason, "We do not serve this region yet.")
        message = OutboundMessage.objects.filter(recipient="rudo@example.com").latest("created")
        self.assertIn("We do not serve this region yet.", message.body)

    def test_an_approved_application_cannot_be_rejected(self):
        application = self.make_application()
        application.status = InstitutionApplication.Status.APPROVED
        application.save(update_fields=["status"])
        with self.assertRaises(ValidationError):
            reject_application(application, self.main, "A reason long enough.")


class ApplicationApprovalTests(MediaMixin, PlatformTestBase):
    def make_application(self, **overrides):
        data = {
            "reference": "APP-2026-APPROVE",
            "institution_name": "Approved Academy",
            "institution_type": "secondary",
            "town": "Mutare",
            "contact_person": "Farai Chikwa",
            "position": "Principal",
            "phone": "+263773333333",
            "email": "farai@example.com",
            "website": "https://approved.example",
            "preferred_plan": self.plans["starter"],
            "modules": [],
            "agreed_to_terms": True,
            "consent_statement_version": "1.0",
            "consent_accepted_at": timezone.now(),
        }
        data.update(overrides)
        return InstitutionApplication.objects.create(**data)

    def test_approval_creates_an_inactive_institution_first_admin_trial_and_welcome(self):
        application = self.make_application()
        self.login(self.main)
        response = self.client.post(
            reverse("platform_admin:application_detail", args=[application.pk]),
            {"action": "approve"},
        )
        self.assertEqual(response.status_code, 302)

        application.refresh_from_db()
        self.assertEqual(application.status, InstitutionApplication.Status.APPROVED)
        self.assertIsNotNone(application.decided_at)
        self.assertIsNotNone(application.created_institution)

        institution = application.created_institution
        self.assertFalse(institution.is_active)
        self.assertEqual(institution.name, "Approved Academy")
        self.assertEqual(institution.type, "secondary")

        admin = institution.users.get(role="admin")
        self.assertEqual(admin.email, "farai@example.com")
        self.assertFalse(admin.has_usable_password())
        self.assertTrue(admin.is_active)
        invitation = UserInvitation.objects.get(user=admin)
        self.assertTrue(invitation.is_open)

        subscription = Subscription.objects.get(institution=institution)
        self.assertEqual(subscription.plan, self.plans["starter"])
        self.assertEqual(subscription.status, Subscription.Status.TRIALING)
        self.assertIsNotNone(subscription.trial_ends_at)

        enabled = set(
            InstitutionModule.objects.filter(institution=institution, enabled=True)
            .values_list("code", flat=True)
        )
        self.assertTrue(set(self.plans["starter"].modules).issubset(enabled))
        self.assertEqual(
            NumberFormat.objects.filter(institution=institution).count(),
            len(__import__("core.numbering", fromlist=["DEFAULTS"]).DEFAULTS),
        )

        consent = DataConsentLog.objects.get(institution=institution)
        self.assertEqual(consent.version, "1.0")
        self.assertTrue(consent.statement_hash)

        welcome = OutboundMessage.objects.filter(recipient="farai@example.com")
        self.assertTrue(welcome.filter(subject__icontains=f"Welcome to {PRODUCT_NAME}").exists())
        self.assertTrue(welcome.filter(body__icontains="choose your password").exists())

    def test_approval_is_exactly_once(self):
        application = self.make_application(
            institution_name="Once Only College", reference="APP-2026-ONCE"
        )
        approve_application(application, self.main)
        # The second call is refused; only one institution exists.
        with self.assertRaises(ValidationError):
            approve_application(application, self.main)
        self.assertEqual(
            Institution.objects.filter(name="Once Only College").count(), 1
        )
        self.assertEqual(Subscription.objects.filter(institution__name="Once Only College").count(), 1)

    def test_posting_approve_twice_does_not_create_a_second_institution(self):
        application = self.make_application(
            institution_name="Double Click School", reference="APP-2026-DOUBLE"
        )
        self.login(self.main)
        url = reverse("platform_admin:application_detail", args=[application.pk])
        self.client.post(url, {"action": "approve"})
        self.client.post(url, {"action": "approve"})
        self.assertEqual(
            Institution.objects.filter(name="Double Click School").count(), 1
        )

    def test_an_administrator_can_approve_with_a_different_plan(self):
        application = self.make_application(
            preferred_plan=self.plans["starter"],
            institution_name="Upgraded College",
            reference="APP-2026-UPGRADE",
        )
        self.login(self.main)
        response = self.client.post(
            reverse("platform_admin:application_detail", args=[application.pk]),
            {"action": "approve", "plan": self.plans["college"].pk},
        )
        self.assertEqual(response.status_code, 302)
        subscription = Subscription.objects.get(institution__name="Upgraded College")
        self.assertEqual(subscription.plan, self.plans["college"])
        self.assertEqual(subscription.status, Subscription.Status.TRIALING)

    def test_the_first_administrator_invitation_is_single_use_and_activates_the_institution(self):
        application = self.make_application(
            institution_name="Invite College",
            reference="APP-2026-INVITE",
            email="invite@example.com",
        )
        institution = approve_application(application, self.main)
        self.assertFalse(institution.is_active)
        admin = institution.users.get(role="admin")
        invitation = UserInvitation.objects.get(user=admin)

        accept_invitation(invitation, STRONG_PASSWORD)
        institution.refresh_from_db()
        admin.refresh_from_db()
        self.assertTrue(institution.is_active)
        self.assertTrue(admin.has_usable_password())

        with self.assertRaises(ValidationError):
            accept_invitation(invitation, STRONG_PASSWORD)

    def test_the_application_and_platform_audit_record_the_decision(self):
        application = self.make_application(
            institution_name="Audited College", reference="APP-2026-AUDITED"
        )
        approve_application(application, self.main)
        application.refresh_from_db()
        self.assertTrue(
            ApplicationEvent.objects.filter(
                application=application, action="approved"
            ).exists()
        )
        from platform_admin.models import PlatformAuditLog

        entry = PlatformAuditLog.objects.get(action="application.approved")
        self.assertEqual(entry.actor, self.main)
        self.assertEqual(entry.institution, application.created_institution)
        self.assertEqual(entry.detail["reference"], application.reference)
        self.assertTrue(PlatformAuditLog.verify_chain())
