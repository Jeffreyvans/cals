"""End-to-end tests for platform-admin institution onboarding."""

from __future__ import annotations

import json
from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from accounts.models import ReservedUsername, UserInvitation
from accounts.user_admin import accept_invitation
from core.models import AuditEntry, Institution, OutboundMessage
from core.notifications import absolute_url
from platform_admin.models import InstitutionOnboarding, PlatformAuditLog, Subscription
from platform_admin.onboarding import add_administrator, admin_checklist

from .base import STRONG_PASSWORD, PlatformTestBase


def institution_payload(**overrides):
    data = {
        "name": "Onboarding Academy",
        "slug": "onboarding-academy",
        "type": "secondary",
        "contact_email": "office@onboarding.example",
        "contact_phone": "+263771111111",
        "address": "12 Main Road, Harare",
        "website": "",
        "access_grant_max_hours": 24,
        "public_page_enabled": "on",
        "action": "edit",
    }
    data.update(overrides)
    return data


class OnboardingFlowTests(PlatformTestBase):
    def create_institution(self, name="Onboarding Academy", slug="onboarding-academy"):
        self.login(self.main)
        response = self.client.post(
            reverse("platform_admin:institution_create"),
            institution_payload(name=name, slug=slug),
        )
        self.assertEqual(response.status_code, 302)
        from core.models import Institution

        return Institution.objects.get(slug=slug)

    def test_platform_admin_creates_institution_and_first_admin_by_invitation(self):
        institution = self.create_institution()
        url = reverse("platform_admin:onboarding_admin_create", args=[institution.pk])
        self.assertEqual(self.client.get(url).status_code, 200)

        response = self.client.post(url, {
            "full_name": "Farai Moyo",
            "email": "farai@onboarding.example",
            "phone": "+263772222222",
            "access_mode": "invite",
        })
        self.assertEqual(response.status_code, 302)
        admin = institution.users.get(email="farai@onboarding.example")
        self.assertEqual(admin.role, "admin")
        self.assertEqual(admin.institution_id, institution.pk)
        self.assertFalse(admin.is_platform_admin)
        self.assertFalse(admin.has_usable_password())
        self.assertTrue(admin.is_active)

        invitation = UserInvitation.objects.get(user=admin)
        self.assertAlmostEqual(
            invitation.expires_at - invitation.created,
            timedelta(hours=72), delta=timedelta(seconds=5),
        )

        # The one-time link was shown exactly once.
        credential_url = reverse(
            "platform_admin:onboarding_credential", args=[institution.pk, admin.pk]
        )
        page = self.client.get(credential_url)
        self.assertContains(page, str(invitation.token))
        self.assertContains(page, "Copy")
        self.assertContains(page, "Print")
        again = self.client.get(credential_url)
        self.assertEqual(again.status_code, 302)

        # The welcome email goes through the central email function exactly once.
        welcome = OutboundMessage.objects.filter(
            recipient=admin.email, subject__icontains="Welcome to EduSphere"
        )
        self.assertEqual(welcome.count(), 1)
        self.assertIn("Sign in", welcome.get().body)
        self.assertIn("+263774573765", welcome.get().body)
        self.assertIn("sphereeedu@gmail.com", welcome.get().body)

        # The institution, not the administrator, is notified separately.
        self.assertTrue(
            OutboundMessage.objects.filter(
                recipient=institution.contact_email,
                subject__icontains="new administrator",
            ).exists()
        )

    def test_platform_admin_creates_first_admin_with_temporary_password(self):
        institution = self.create_institution("Temp Password College", "temp-password-college")
        url = reverse("platform_admin:onboarding_admin_create", args=[institution.pk])
        response = self.client.post(url, {
            "full_name": "Tadiwa Ndlovu",
            "email": "tadiwa@onboarding.example",
            "phone": "",
            "access_mode": "temp_password",
        })
        self.assertEqual(response.status_code, 302)
        admin = institution.users.get(email="tadiwa@onboarding.example")
        self.assertTrue(admin.has_usable_password())
        self.assertTrue(admin.must_change_password)

        credential_url = reverse(
            "platform_admin:onboarding_credential", args=[institution.pk, admin.pk]
        )
        page = self.client.get(credential_url)
        self.assertContains(page, "Temporary password")
        secret = page.context["secret"]
        self.assertTrue(secret)

        self.client.logout()
        login = self.client.post(
            reverse("login"), {"username": admin.username, "password": secret}
        )
        self.assertEqual(login.status_code, 302)
        self.assertEqual(login.url, "/dashboard/")
        # The middleware forces the change on the next page, not at the POST.
        protected = self.client.get(login.url)
        self.assertEqual(protected.status_code, 302)
        self.assertEqual(protected.url, "/password-change/?next=/dashboard/")

        # No password is stored in plain text or written to an audit row.
        self.assertNotIn(secret, json.dumps(
            list(PlatformAuditLog.objects.values("detail", "before", "after"))
        ))
        self.assertFalse(
            PlatformAuditLog.objects.filter(detail__icontains=secret).exists()
        )
        self.client.get(reverse("logout"))

    def test_invitation_works_once_and_expires(self):
        institution = self.create_institution("Invite College", "invite-college")
        admin = add_administrator(
            institution,
            full_name="Invite Admin",
            email="invite@onboarding.example",
            access_mode="invitation",
            actor=self.main,
        ).user
        invitation = UserInvitation.objects.get(user=admin)
        accept_invitation(invitation, STRONG_PASSWORD)
        with self.assertRaises(ValidationError):
            accept_invitation(invitation, STRONG_PASSWORD)

        second = add_administrator(
            institution,
            full_name="Second Admin",
            email="second@onboarding.example",
            access_mode="invitation",
            actor=self.main,
        ).user
        expired = UserInvitation.objects.get(user=second)
        expired.expires_at = timezone.now() - timedelta(minutes=1)
        expired.save(update_fields=["expires_at"])
        with self.assertRaises(ValidationError):
            accept_invitation(expired, STRONG_PASSWORD)

    def test_administrator_is_scoped_to_institution_and_cannot_open_platform(self):
        institution = self.create_institution("Scoped College", "scoped-college")
        admin = add_administrator(
            institution,
            full_name="Scoped Admin",
            email="scoped@onboarding.example",
            access_mode="invitation",
            actor=self.main,
        ).user
        invitation = UserInvitation.objects.get(user=admin)
        accept_invitation(invitation, STRONG_PASSWORD)
        # The invitation service saves a new password hash on its own instance.
        admin.refresh_from_db()

        self.client.logout()
        self.client.force_login(admin)
        self.assertEqual(self.client.get("/platform/").status_code, 404)
        self.assertEqual(
            self.client.get(
                reverse("platform_admin:onboarding_admin_create", args=[institution.pk])
            ).status_code,
            404,
        )
        from django.contrib.auth import get_user_model

        self.assertEqual(
            get_user_model().objects.filter(username=admin.username).count(), 1
        )

    def test_last_active_administrator_cannot_be_deactivated_or_removed(self):
        institution = self.create_institution("Last Admin College", "last-admin-college")
        admin = add_administrator(
            institution,
            full_name="Only Admin",
            email="only@onboarding.example",
            access_mode="temporary_password",
            actor=self.main,
        ).user
        admins_url = reverse("platform_admin:institution_admins", args=[institution.pk])
        for action in ("deactivate", "remove"):
            response = self.client.post(admins_url, {"user": admin.pk, "action": action})
            self.assertEqual(response.status_code, 302)
            admin.refresh_from_db()
            self.assertTrue(admin.is_active)

        add_administrator(
            institution,
            full_name="Backup Admin",
            email="backup@onboarding.example",
            access_mode="temporary_password",
            actor=self.main,
        )
        response = self.client.post(admins_url, {"user": admin.pk, "action": "remove"})
        self.assertEqual(response.status_code, 302)
        admin.refresh_from_db()
        self.assertFalse(admin.is_active)

    def test_non_platform_users_cannot_add_administrators(self):
        institution = self.create_institution("Guarded College", "guarded-college")
        url = reverse("platform_admin:onboarding_admin_create", args=[institution.pk])
        for user in (self.inst_admin, self.other_admin, self.teacher):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(
                self.client.post(url, {"full_name": "Nope", "access_mode": "invite"}).status_code,
                404,
            )

    def test_checklist_updates_as_steps_complete(self):
        institution = self.create_institution()
        first = admin_checklist(institution)
        keys = {item["key"] for item in first["items"]}
        self.assertEqual(
            keys,
            {"administrator", "signed_in", "logo", "academic_year",
             "modules", "first_users", "plan"},
        )
        before = {item["key"]: item["done"] for item in first["items"]}
        self.assertFalse(before["administrator"])
        self.assertTrue(before["modules"])
        self.assertFalse(before["plan"])

        admin = add_administrator(
            institution,
            full_name="Checklist Admin",
            email="checklist@onboarding.example",
            access_mode="invitation",
            actor=self.main,
        ).user
        self.assertTrue(admin_checklist(institution)["items"][0]["done"])
        self.assertEqual(
            InstitutionOnboarding.objects.get(institution=institution).first_admin_created_at.date(),
            timezone.localdate(),
        )

        invitation = UserInvitation.objects.get(user=admin)
        accept_invitation(invitation, STRONG_PASSWORD)
        self.client.logout()
        self.assertTrue(self.client.login(username=admin.username, password=STRONG_PASSWORD))
        self.assertIsNotNone(
            InstitutionOnboarding.objects.get(institution=institution).first_admin_login_at
        )

        institution.logo = "institutions/logo.png"
        institution.save(update_fields=["logo"])
        from academics.models import AcademicYear

        AcademicYear.all_objects.create(
            institution=institution, name="2026",
            start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
        )
        from django.contrib.auth import get_user_model

        get_user_model().objects.create_user(
            "checkliststudent", email="student@onboarding.example",
            role="student", institution=institution, password=STRONG_PASSWORD,
        )
        Subscription.objects.create(institution=institution, plan=self.plans["starter"])
        final = admin_checklist(institution)
        self.assertEqual(final["done"], 7)
        self.assertEqual(final["percent"], 100)
        self.assertEqual(final["missing"], [])

    def test_every_onboarding_action_is_audited_without_secrets(self):
        institution = self.create_institution()
        result = add_administrator(
            institution,
            full_name="Audited Admin",
            email="audited@onboarding.example",
            access_mode="temporary_password",
            actor=self.main,
        )
        actions = set(PlatformAuditLog.objects.values_list("action", flat=True))
        self.assertIn("institution.created", actions)
        self.assertIn("institution.admin_created", actions)
        self.assertIn("institution.welcome_email_sent", actions)
        self.assertTrue(PlatformAuditLog.objects.filter(institution=institution).exists())
        serialized = json.dumps(
            list(PlatformAuditLog.objects.values("detail", "before", "after"))
        )
        self.assertNotIn(result.secret, serialized)
        self.assertNotIn(result.user.password, serialized)

    def test_welcome_email_can_be_resent_by_platform_admin(self):
        institution = self.create_institution()
        admin = add_administrator(
            institution,
            full_name="Resend Admin",
            email="resend@onboarding.example",
            access_mode="invitation",
            actor=self.main,
        ).user
        welcome = OutboundMessage.objects.filter(
            recipient=admin.email, subject__icontains="Welcome to EduSphere"
        )
        self.assertEqual(welcome.count(), 1)
        response = self.client.post(
            reverse("platform_admin:institution_detail", args=[institution.pk]),
            {"action": "resend_welcome", "user": admin.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(welcome.count(), 2)
        self.assertTrue(
            PlatformAuditLog.objects.filter(action="institution.welcome_email_resent").exists()
        )

    def test_absolute_url_uses_site_url_for_login_links(self):
        institution = self.create_institution()
        admin = add_administrator(
            institution,
            full_name="Link Admin",
            email="link@onboarding.example",
            access_mode="temporary_password",
            actor=self.main,
        ).user
        message = OutboundMessage.objects.get(
            recipient=admin.email, subject__icontains="Welcome to EduSphere"
        )
        self.assertIn(absolute_url(f"/i/{institution.slug}/login/"), message.body)

    def test_add_admin_form_shows_access_options_and_accepts_each(self):
        institution = self.create_institution("Access Options College", "access-options-college")
        url = reverse("platform_admin:onboarding_admin_create", args=[institution.pk])

        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "How should this person get access?")
        self.assertContains(page, "Send an invitation email")
        self.assertContains(page, "Create a temporary password")

        invite = self.client.post(url, {
            "full_name": "Invite Admin",
            "email": "invite-admin@onboarding.example",
            "phone": "",
            "access_mode": "invite",
        })
        self.assertEqual(invite.status_code, 302)
        invite_user = institution.users.get(email="invite-admin@onboarding.example")
        self.assertRedirects(
            invite,
            reverse("platform_admin:onboarding_credential", args=[institution.pk, invite_user.pk]),
            fetch_redirect_response=False,
        )

        temp = self.client.post(url, {
            "full_name": "Temp Admin",
            "email": "temp-admin@onboarding.example",
            "phone": "",
            "access_mode": "temp_password",
        })
        self.assertEqual(temp.status_code, 302)
        temp_user = institution.users.get(email="temp-admin@onboarding.example")
        self.assertRedirects(
            temp,
            reverse("platform_admin:onboarding_credential", args=[institution.pk, temp_user.pk]),
            fetch_redirect_response=False,
        )


class AdministratorUsernameTests(PlatformTestBase):
    """The optional username field and the platform administrator rename flow."""

    def create_institution(self, name="Username College", slug="username-college"):
        self.login(self.main)
        response = self.client.post(
            reverse("platform_admin:institution_create"),
            institution_payload(name=name, slug=slug),
        )
        self.assertEqual(response.status_code, 302)
        return Institution.objects.get(slug=slug)

    @staticmethod
    def sign_in_instruction(institution, username):
        login_url = absolute_url(reverse("institution_login", args=[institution.slug]))
        return f"Sign in at {login_url} with your email or username {username}."

    def test_add_form_has_optional_username_after_full_name(self):
        institution = self.create_institution()
        page = self.client.get(
            reverse("platform_admin:onboarding_admin_create", args=[institution.pk])
        )
        self.assertEqual(page.status_code, 200)
        content = page.content.decode()
        names = ("full_name", "username", "email", "phone", "access_mode")
        positions = [content.index(f'name="{name}"') for name in names]
        self.assertEqual(positions, sorted(positions))
        self.assertContains(
            page, "Leave blank and we will create one from the email address."
        )
        self.assertContains(page, "js/username-check.js")

    def test_blank_username_is_generated_from_email_and_shown_everywhere(self):
        institution = self.create_institution()
        response = self.client.post(
            reverse("platform_admin:onboarding_admin_create", args=[institution.pk]),
            {
                "full_name": "Generated Person",
                "username": "",
                "email": "generated.person@onboarding.example",
                "access_mode": "invite",
            },
        )
        self.assertEqual(response.status_code, 302)
        admin = institution.users.get(email="generated.person@onboarding.example")
        self.assertEqual(admin.username, "generated.person")
        instruction = self.sign_in_instruction(institution, admin.username)

        credential_url = reverse(
            "platform_admin:onboarding_credential", args=[institution.pk, admin.pk]
        )
        page = self.client.get(credential_url)
        self.assertContains(page, admin.username)
        self.assertContains(
            page,
            f"with your email or username <strong>{admin.username}</strong>.",
        )
        self.assertContains(page, "Copy")
        self.assertContains(page, "Print")

        invitation = OutboundMessage.objects.get(
            recipient=admin.email, subject__icontains="Your"
        )
        welcome = OutboundMessage.objects.get(
            recipient=admin.email, subject__icontains="Welcome to EduSphere"
        )
        self.assertIn(instruction, invitation.body)
        self.assertIn(instruction, welcome.body)

    def test_provided_username_is_normalised_and_bad_names_are_field_errors(self):
        institution = self.create_institution()
        url = reverse("platform_admin:onboarding_admin_create", args=[institution.pk])

        response = self.client.post(url, {
            "full_name": "Mixed Case",
            "username": "Mixed.Case_2026",
            "email": "mixed.case@onboarding.example",
            "access_mode": "invite",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            institution.users.filter(username="mixed.case_2026").exists()
        )

        cases = (
            ("PLATFORMMAIN", "already in use", "duplicate@example.test"),
            ("ADMIN", "reserved", "reserved@example.test"),
            ("two words", "spaces", "spaces@example.test"),
            ("abc", "4 to 30", "short@example.test"),
        )
        for username, message, email in cases:
            with self.subTest(username=username):
                response = self.client.post(url, {
                    "full_name": "Bad Name",
                    "username": username,
                    "email": email,
                    "access_mode": "invite",
                })
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors["username"])
                self.assertIn(message, " ".join(response.context["form"].errors["username"]))
                self.assertFalse(institution.users.filter(email=email).exists())

    def test_temporary_password_screen_shows_username_and_secret_once(self):
        institution = self.create_institution()
        response = self.client.post(
            reverse("platform_admin:onboarding_admin_create", args=[institution.pk]),
            {
                "full_name": "Temporary Person",
                "username": "temp.person",
                "email": "temp.person@onboarding.example",
                "access_mode": "temp_password",
            },
        )
        self.assertEqual(response.status_code, 302)
        admin = institution.users.get(email="temp.person@onboarding.example")
        page = self.client.get(
            reverse("platform_admin:onboarding_credential", args=[institution.pk, admin.pk])
        )
        secret = page.context["secret"]
        self.assertTrue(secret)
        self.assertContains(page, "Temporary password")
        self.assertContains(page, admin.username)
        self.assertContains(
            page,
            f"with your email or username <strong>{admin.username}</strong>.",
        )
        self.assertContains(page, "Copy")
        self.assertContains(page, "Print")
        for message in OutboundMessage.objects.filter(recipient=admin.email):
            self.assertNotIn(secret, message.body)
            self.assertNotIn(secret, message.html_body)

    def test_platform_admin_can_edit_username_with_audit_and_email(self):
        institution = self.create_institution()
        admin = add_administrator(
            institution,
            full_name="Rename Me",
            email="rename.me@onboarding.example",
            access_mode="invitation",
            actor=self.main,
        ).user
        old_username = admin.username
        url = reverse(
            "platform_admin:onboarding_admin_username",
            args=[institution.pk, admin.pk],
        )
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "js/username-check.js")
        self.assertContains(page, old_username)

        response = self.client.post(url, {"username": "RENAMED.PERSON"})
        self.assertEqual(response.status_code, 302)
        admin.refresh_from_db()
        self.assertEqual(admin.username, "renamed.person")

        audit = PlatformAuditLog.objects.get(
            action="institution.admin_username_changed",
            record_id=str(admin.pk),
        )
        self.assertEqual(audit.before, {"username": old_username})
        self.assertEqual(audit.after, {"username": "renamed.person"})
        self.assertEqual(audit.actor, self.main)
        self.assertTrue(
            AuditEntry.all_objects.filter(
                action="user.username_changed", institution=institution
            ).exists()
        )
        self.assertTrue(ReservedUsername.objects.filter(username=old_username).exists())

        notices = OutboundMessage.objects.filter(
            recipient=admin.email, subject__icontains="username has changed"
        )
        self.assertTrue(notices.exists())
        self.assertTrue(
            any(
                self.sign_in_instruction(institution, "renamed.person") in notice.body
                for notice in notices
            )
        )
