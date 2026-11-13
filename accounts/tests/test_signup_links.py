"""Part 2: shareable public sign-up links.

An administrator can create a link, hand it out, and let people register
themselves at that one institution. A link can be limited to students or staff,
preset to a staff position, expire, or run out of uses. These tests cover the
model, the service, the public page and the administrator screen.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from academics.models import Programme, Student
from accounts.models import SignupLink, SignupLinkUse, StaffRegistrationRequest
from accounts.registration import STAFF_GENERIC_PENDING, STUDENT_GENERIC_ERROR
from accounts.signup_links import (
    create_signup_link,
    delete_signup_link,
    link_offers,
    link_positions,
    reactivate_signup_link,
    revoke_signup_link,
)
from core.models import AuditEntry, Institution
from core.roles import ensure_roles
from core.tenancy import use_institution

User = get_user_model()
STRONG = "Str0ngPassw0rd"


class SignupLinkModelTests(TestCase):
    def setUp(self):
        self.inst = Institution.objects.create(name="Alpha College", slug="alpha")
        self.link = SignupLink.objects.create(institution=self.inst)

    def test_a_new_link_is_open(self):
        self.assertTrue(self.link.is_open)
        self.assertFalse(self.link.is_expired)
        self.assertFalse(self.link.is_exhausted)
        self.assertEqual(self.link.use_count, 0)

    def test_an_expired_link_is_closed(self):
        self.link.expires_at = timezone.now() - timezone.timedelta(hours=1)
        self.link.save(update_fields=["expires_at"])
        self.assertTrue(self.link.is_expired)
        self.assertFalse(self.link.is_open)

    def test_an_inactive_link_is_closed(self):
        self.link.is_active = False
        self.link.save(update_fields=["is_active"])
        self.assertFalse(self.link.is_open)

    def test_a_used_up_link_is_closed(self):
        self.link.max_uses = 1
        self.link.save(update_fields=["max_uses"])
        SignupLinkUse.objects.create(link=self.link, kind="student")
        self.assertTrue(self.link.is_exhausted)
        self.assertFalse(self.link.is_open)

    def test_the_path_and_url_use_the_token(self):
        self.assertIn(str(self.link.token), self.link.path())
        self.assertEqual(self.link.url(), self.link.path())

    def test_the_position_label_is_readable(self):
        self.link.position = "librarian"
        self.assertEqual(self.link.position_label, "Librarian")


class SignupLinkServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.inst = Institution.objects.create(
            name="Alpha College", slug="alpha", staff_positions=["teacher", "bursar"])
        cls.admin = User.objects.create_user(
            "alphaadmin", password=STRONG, role="admin", institution=cls.inst)

    def test_creating_a_link_is_audited(self):
        link = create_signup_link(
            self.inst, label="Term 1", kind=SignupLink.Kind.STAFF,
            position="teacher", actor=self.admin)
        self.assertTrue(link.pk)
        entry = AuditEntry.all_objects.filter(
            action="signup_link.created", institution=self.inst).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.after["position"], "teacher")

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(ValidationError):
            create_signup_link(self.inst, kind="aliens")

    def test_a_forbidden_position_is_refused(self):
        with self.assertRaises(ValidationError):
            create_signup_link(
                self.inst, kind=SignupLink.Kind.STAFF, position="administrator")

    def test_a_student_link_cannot_carry_a_position(self):
        with self.assertRaises(ValidationError):
            create_signup_link(
                self.inst, kind=SignupLink.Kind.STUDENT, position="teacher")

    def test_a_bad_use_limit_is_refused(self):
        with self.assertRaises(ValidationError):
            create_signup_link(self.inst, max_uses=0)
        with self.assertRaises(ValidationError):
            create_signup_link(self.inst, max_uses="lots")

    def test_revoking_and_reactivating_a_link(self):
        link = create_signup_link(self.inst, actor=self.admin)
        revoke_signup_link(link, actor=self.admin)
        link.refresh_from_db()
        self.assertFalse(link.is_active)
        reactivate_signup_link(link, actor=self.admin)
        link.refresh_from_db()
        self.assertTrue(link.is_active)
        self.assertTrue(AuditEntry.all_objects.filter(
            action="signup_link.revoked", institution=self.inst).exists())
        self.assertTrue(AuditEntry.all_objects.filter(
            action="signup_link.reactivated", institution=self.inst).exists())

    def test_deleting_a_link_keeps_an_audit_row(self):
        link = create_signup_link(self.inst, actor=self.admin)
        pk = link.pk
        delete_signup_link(link, actor=self.admin)
        self.assertFalse(SignupLink.objects.filter(pk=pk).exists())
        self.assertTrue(AuditEntry.all_objects.filter(
            action="signup_link.deleted", institution=self.inst).exists())

    def test_a_preset_position_wins(self):
        link = create_signup_link(
            self.inst, kind=SignupLink.Kind.STAFF, position="bursar")
        self.assertEqual(link_positions(link), ["bursar"])

    def test_an_unpreset_staff_link_uses_the_institution_list(self):
        link = create_signup_link(self.inst, kind=SignupLink.Kind.STAFF)
        self.assertEqual(link_positions(link), ["teacher", "bursar"])

    def test_an_unpreset_staff_link_falls_back_to_every_position(self):
        bare = Institution.objects.create(name="Bare School", slug="bare")
        link = create_signup_link(bare, kind=SignupLink.Kind.STAFF)
        positions = link_positions(link)
        self.assertIn("teacher", positions)
        self.assertNotIn("administrator", positions)

    def test_link_offers_follow_the_kind(self):
        self.assertEqual(
            link_offers(SignupLink(kind=SignupLink.Kind.STUDENT)), {"student"})
        self.assertEqual(
            link_offers(SignupLink(kind=SignupLink.Kind.STAFF)), {"staff"})
        self.assertEqual(
            link_offers(SignupLink(kind=SignupLink.Kind.ANY)),
            {"student", "staff"})


class SignupLinkPublicTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.inst = Institution.objects.create(
            name="Mavambo College",
            slug="mavambo",
            public_registration=False,
            staff_positions=[],
            contact_email="registrar@mavambo.test",
            contact_phone="+263771000111",
        )
        with use_institution(cls.inst):
            cls.programme = Programme.objects.create(
                institution=cls.inst, name="Diploma in IT", code="DIT",
                level=Programme.Level.DIPLOMA)
        cls.student_user = User.objects.create_user(
            "oldlogin", password="OldPass1234", role=User.Role.STUDENT,
            institution=cls.inst, first_name="Tariro", last_name="Moyo")
        cls.student_user.set_unusable_password()
        cls.student_user.is_active = False
        cls.student_user.save(update_fields=["password", "is_active"])
        with use_institution(cls.inst):
            cls.student = Student.objects.create(
                institution=cls.inst, user=cls.student_user,
                programme=cls.programme, student_no="ST-001",
                date_of_birth=date(2010, 5, 4))

    def setUp(self):
        cache.clear()

    def _link(self, **kwargs):
        return create_signup_link(self.inst, **kwargs)

    def _student_payload(self, link, **overrides):
        data = {
            "path": "student",
            "institution": self.inst.pk,
            "registration_number": "ST-001",
            "surname": "Moyo",
            "date_of_birth": "2010-05-04",
            "contact": "tariro.new@example.test",
            "username": "tariro2",
            "password": STRONG,
        }
        data.update(overrides)
        return data

    def _staff_payload(self, link, **overrides):
        data = {
            "path": "staff",
            "institution": self.inst.pk,
            "full_name": "Jane Doe",
            "email": "jane@example.test",
            "phone": "+263771000001",
            "position": "librarian",
            "username": "janedoe",
            "password": STRONG,
        }
        data.update(overrides)
        return data

    def test_an_unknown_token_is_a_404(self):
        resp = self.client.get(reverse("signup_link", args=[
            "00000000-0000-0000-0000-000000000000"]))
        self.assertEqual(resp.status_code, 404)

    def test_an_open_link_shows_the_institution(self):
        link = self._link(kind=SignupLink.Kind.STUDENT)
        resp = self.client.get(reverse("signup_link", args=[link.token]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Mavambo College")
        self.assertContains(resp, "registrar@mavambo.test")

    def test_a_student_link_goes_straight_to_the_student_form(self):
        link = self._link(kind=SignupLink.Kind.STUDENT)
        resp = self.client.get(reverse("signup_link", args=[link.token]))
        self.assertContains(resp, "Match your admission record")

    def test_an_any_link_shows_a_chooser(self):
        link = self._link(kind=SignupLink.Kind.ANY)
        resp = self.client.get(reverse("signup_link", args=[link.token]))
        self.assertContains(resp, "How would you like to join?")
        self.assertContains(resp, "as a student")
        self.assertContains(resp, "as staff")

    def test_a_staff_link_presets_its_position(self):
        link = self._link(kind=SignupLink.Kind.STAFF, position="librarian")
        resp = self.client.get(reverse("signup_link", args=[link.token]))
        self.assertContains(resp, "Librarian")

    def test_a_closed_link_shows_a_message(self):
        link = self._link(kind=SignupLink.Kind.ANY)
        revoke_signup_link(link)
        resp = self.client.get(reverse("signup_link", args=[link.token]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "closed")

    def test_an_expired_link_is_closed(self):
        link = self._link(kind=SignupLink.Kind.ANY)
        link.expires_at = timezone.now() - timezone.timedelta(hours=1)
        link.save(update_fields=["expires_at"])
        resp = self.client.get(reverse("signup_link", args=[link.token]))
        self.assertContains(resp, "closed")

    def test_a_student_registers_even_when_public_registration_is_off(self):
        link = self._link(kind=SignupLink.Kind.STUDENT)
        resp = self.client.post(
            reverse("signup_link", args=[link.token]),
            self._student_payload(link),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Your account is ready")
        self.student_user.refresh_from_db()
        self.assertTrue(self.student_user.is_active)
        self.assertEqual(SignupLinkUse.objects.filter(link=link, kind="student").count(), 1)

    def test_a_student_mismatch_shows_the_generic_error(self):
        link = self._link(kind=SignupLink.Kind.STUDENT)
        resp = self.client.post(
            reverse("signup_link", args=[link.token]),
            self._student_payload(link, surname="Wrong"),
        )
        # The message contains an apostrophe, which the template escapes, so
        # match on the part before it.
        self.assertContains(resp, STUDENT_GENERIC_ERROR.split(",")[0])
        self.assertEqual(SignupLinkUse.objects.filter(link=link).count(), 0)

    def test_staff_registers_through_the_link_for_a_preset_position(self):
        link = self._link(kind=SignupLink.Kind.STAFF, position="librarian")
        resp = self.client.post(
            reverse("signup_link", args=[link.token]),
            self._staff_payload(link, position="librarian"),
        )
        # The message contains an apostrophe, which the template escapes, so
        # match on the part before it.
        self.assertContains(resp, STAFF_GENERIC_PENDING.split(".")[0])
        request = StaffRegistrationRequest.objects.get(email="jane@example.test")
        self.assertEqual(request.position, "librarian")
        self.assertFalse(request.user.is_active)
        self.assertEqual(SignupLinkUse.objects.filter(link=link, kind="staff").count(), 1)

    def test_a_used_up_link_stops_working(self):
        link = self._link(kind=SignupLink.Kind.STUDENT, max_uses=1)
        self.client.post(
            reverse("signup_link", args=[link.token]),
            self._student_payload(link),
        )
        resp = self.client.get(reverse("signup_link", args=[link.token]))
        self.assertContains(resp, "closed")

    def test_too_many_attempts_are_rate_limited(self):
        link = self._link(kind=SignupLink.Kind.STUDENT)
        cache.set(f"edusphere.signup.{link.pk}.127.0.0.1", 10, 900)
        resp = self.client.get(reverse("signup_link", args=[link.token]))
        self.assertEqual(resp.status_code, 429)


class SignupLinkAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.inst = Institution.objects.create(
            name="Alpha College", slug="alpha", type="college")
        cls.other = Institution.objects.create(
            name="Beta College", slug="beta", type="college")
        cls.roles = ensure_roles(cls.inst)
        ensure_roles(cls.other)
        cls.admin = User.objects.create_user(
            "alphaadmin", password=STRONG, role="admin", institution=cls.inst)
        cls.admin.primary_role = cls.roles["institution_admin"]
        cls.admin.save(update_fields=["primary_role"])
        cls.student = User.objects.create_user(
            "alphastudent", password=STRONG, role="student", institution=cls.inst)

    def setUp(self):
        cache.clear()

    def test_the_page_needs_the_users_permission(self):
        self.client.force_login(self.student)
        resp = self.client.get(reverse("signup_link_list"))
        self.assertEqual(resp.status_code, 403)

    def test_an_administrator_creates_a_link(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("signup_link_list"), {
            "label": "Term 1 intake",
            "kind": SignupLink.Kind.STAFF,
            "position": "teacher",
            "expires_on": "2026-12-31",
            "max_uses": "5",
        })
        self.assertRedirects(resp, reverse("signup_link_list"))
        link = SignupLink.objects.get(institution=self.inst)
        self.assertEqual(link.label, "Term 1 intake")
        self.assertEqual(link.position, "teacher")
        self.assertEqual(link.max_uses, 5)
        self.assertIsNotNone(link.expires_at)

    def test_a_bad_expiry_date_is_refused(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("signup_link_list"), {
            "kind": SignupLink.Kind.ANY, "expires_on": "not-a-date"})
        self.assertFalse(SignupLink.objects.filter(institution=self.inst).exists())

    def test_the_list_shows_the_share_url(self):
        self.client.force_login(self.admin)
        link = create_signup_link(self.inst, kind=SignupLink.Kind.ANY)
        resp = self.client.get(reverse("signup_link_list"))
        self.assertContains(resp, str(link.token))
        self.assertContains(resp, "Sign-up links")

    def test_revoking_and_deleting_from_the_screen(self):
        self.client.force_login(self.admin)
        link = create_signup_link(self.inst, kind=SignupLink.Kind.ANY)
        self.client.post(
            reverse("signup_link_action", args=[link.pk]), {"action": "revoke"})
        link.refresh_from_db()
        self.assertFalse(link.is_active)
        self.client.post(
            reverse("signup_link_action", args=[link.pk]), {"action": "delete"})
        self.assertFalse(SignupLink.objects.filter(pk=link.pk).exists())

    def test_another_institution_cannot_reach_the_link(self):
        other_admin = User.objects.create_user(
            "betaadmin", password=STRONG, role="admin", institution=self.other)
        other_admin.primary_role = other_admin.institution.roles.get(
            code="institution_admin")
        other_admin.save(update_fields=["primary_role"])
        link = create_signup_link(self.inst, kind=SignupLink.Kind.ANY)
        self.client.force_login(other_admin)
        resp = self.client.post(
            reverse("signup_link_action", args=[link.pk]), {"action": "revoke"})
        self.assertEqual(resp.status_code, 404)
        link.refresh_from_db()
        self.assertTrue(link.is_active)

    def test_the_list_only_shows_the_users_own_institution(self):
        create_signup_link(self.inst, label="Alpha link")
        create_signup_link(self.other, label="Beta link")
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("signup_link_list"))
        self.assertContains(resp, "Alpha link")
        self.assertNotContains(resp, "Beta link")

    def test_the_settings_screen_links_to_the_signup_links(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("institution_settings"))
        self.assertContains(resp, reverse("signup_link_list"))
