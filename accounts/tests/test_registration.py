"""The public landing page and the two registration paths."""

from datetime import date

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils.html import escape

from academics.models import Programme, Student
from accounts.models import StaffRegistrationRequest
from accounts.registration import (
    STAFF_GENERIC_PENDING,
    STUDENT_GENERIC_ERROR,
    register_staff,
)
from core.models import Institution
from core.tenancy import use_institution

User = get_user_model()
STRONG = "Str0ngPassw0rd"


class RegistrationBase(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox = []
        self.institution = Institution.objects.create(
            name="Mavambo College",
            slug="mavambo",
            public_registration=True,
            staff_positions=["teacher", "bursar", "librarian"],
            contact_email="registrar@mavambo.test",
            contact_phone="+263771000111",
        )
        self.other_institution = Institution.objects.create(
            name="Riverside School",
            slug="riverside",
            public_registration=True,
            staff_positions=["teacher"],
            contact_email="office@riverside.test",
            contact_phone="+263772000222",
        )
        with use_institution(self.institution):
            self.programme = Programme.objects.create(
                institution=self.institution,
                name="Diploma in IT",
                code="DIT",
                level=Programme.Level.DIPLOMA,
            )
        self.student_user = User.objects.create_user(
            "oldlogin",
            password="OldPass1234",
            role=User.Role.STUDENT,
            institution=self.institution,
            first_name="Tariro",
            last_name="Moyo",
        )
        self.student_user.set_unusable_password()
        self.student_user.is_active = False
        self.student_user.save(update_fields=["password", "is_active"])
        with use_institution(self.institution):
            self.student = Student.objects.create(
                institution=self.institution,
                user=self.student_user,
                programme=self.programme,
                student_no="ST-001",
                date_of_birth=date(2010, 5, 4),
            )
        self.admin = User.objects.create_user(
            "admin",
            password="AdminPass1234",
            role=User.Role.ADMIN,
            institution=self.institution,
            is_superuser=True,
            is_staff=True,
        )
        self.other_admin = User.objects.create_user(
            "otheradmin",
            password="AdminPass1234",
            role=User.Role.ADMIN,
            institution=self.other_institution,
            is_superuser=True,
            is_staff=True,
        )

    def student_payload(self, **overrides):
        payload = {
            "institution": self.institution.pk,
            "registration_number": "ST-001",
            "surname": "Moyo",
            "date_of_birth": "2010-05-04",
            "contact": "tariro@example.test",
            "username": "tariro",
            "password": STRONG,
        }
        payload.update(overrides)
        return payload

    def staff_payload(self, **overrides):
        payload = {
            "institution": self.institution.pk,
            "full_name": "Farai Ncube",
            "email": "farai@example.test",
            "phone": "0771234567",
            "position": "teacher",
            "department": "",
            "staff_no": "S-10",
            "username": "farai",
            "password": STRONG,
            "message": "Please consider my request.",
        }
        payload.update(overrides)
        return payload


class LandingPageTests(RegistrationBase):
    def test_landing_page_names_no_institution_and_shows_contact_details(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Lindenet")
        self.assertNotContains(response, self.institution.name)
        self.assertContains(response, "EduSphere")
        self.assertContains(response, "+263774573765")
        self.assertContains(response, "vanhuvaonejeffrey@gmail.com")
        self.assertContains(response, 'name="username"')
        self.assertContains(response, 'name="password"')

    def test_sign_in_still_works(self):
        response = self.client.post(
            reverse("login"),
            {"username": "admin", "password": "AdminPass1234"},
        )
        self.assertEqual(response.status_code, 302)

    def test_wrong_password_keeps_the_plain_error(self):
        response = self.client.post(
            reverse("login"),
            {"username": "admin", "password": "wrong"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Wrong username or password. Try again.")

    def test_contact_form_sends_an_email(self):
        response = self.client.post(
            reverse("login"),
            {
                "form": "contact",
                "name": "Head Teacher",
                "email": "head@example.test",
                "phone": "",
                "message": "We would like a demonstration of the finance module.",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Thank you")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("vanhuvaonejeffrey@gmail.com", mail.outbox[0].to)

    def test_contact_honeypot_does_not_send(self):
        response = self.client.post(
            reverse("login"),
            {
                "form": "contact",
                "name": "Bot",
                "email": "bot@example.test",
                "message": "Buy cheap things from this very long message.",
                "website": "https://spam.example",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)


class StudentRegistrationTests(RegistrationBase):
    def test_matching_details_activate_the_portal(self):
        response = self.client.post(
            reverse("register_student"), self.student_payload()
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your account is ready")
        self.student_user.refresh_from_db()
        self.assertTrue(self.student_user.is_active)
        self.assertEqual(self.student_user.username, "tariro")
        self.assertTrue(self.student_user.check_password(STRONG))
        self.assertEqual(self.student_user.email, "tariro@example.test")

    def test_match_ignores_case_and_extra_spaces(self):
        self.client.post(
            reverse("register_student"),
            self.student_payload(
                registration_number="  st-001 ",
                surname="  mOYo ",
            ),
        )
        self.student_user.refresh_from_db()
        self.assertTrue(self.student_user.is_active)
        self.assertEqual(self.student_user.username, "tariro")

    def test_wrong_surname_wrong_date_and_missing_number_share_one_message(self):
        cases = [
            {"surname": "Ncube"},
            {"date_of_birth": "2011-05-04"},
            {"registration_number": "ST-999"},
        ]
        for case in cases:
            with self.subTest(case=case):
                cache.clear()
                response = self.client.post(
                    reverse("register_student"), self.student_payload(**case)
                )
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, escape(STUDENT_GENERIC_ERROR))
                self.assertNotContains(response, "date of birth does not match")
                self.assertNotContains(response, "surname does not match")
                self.student_user.refresh_from_db()
                self.assertFalse(self.student_user.is_active)

    def test_existing_login_is_reported_without_creating_a_second_account(self):
        self.student_user.set_password("OldPass1234")
        self.student_user.is_active = True
        self.student_user.save(update_fields=["password", "is_active"])
        before = User.objects.count()
        response = self.client.post(
            reverse("register_student"), self.student_payload()
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A login already exists for this student")
        self.assertEqual(User.objects.count(), before)

    def test_repeated_mismatches_are_throttled(self):
        for _ in range(5):
            self.client.post(
                reverse("register_student"),
                self.student_payload(surname="Wrong"),
            )
        response = self.client.post(
            reverse("register_student"),
            self.student_payload(surname="Wrong"),
        )
        self.assertEqual(response.status_code, 429)
        self.assertContains(
            response,
            "Too many attempts from this connection",
            status_code=429,
        )

    def test_failed_attempts_are_logged_for_the_institution(self):
        self.client.post(
            reverse("register_student"),
            self.student_payload(surname="Wrong"),
        )
        from core.models import AuditEntry

        self.assertTrue(
            AuditEntry.all_objects.filter(
                institution=self.institution,
                action="registration.student_failed",
            ).exists()
        )

    def test_mismatch_offers_the_institution_contact_page(self):
        response = self.client.post(
            reverse("register_student"),
            self.student_payload(surname="Wrong"),
        )
        self.assertContains(
            response,
            reverse("register_contact", args=[self.institution.pk]),
        )
        contact = self.client.get(
            reverse("register_contact", args=[self.institution.pk])
        )
        self.assertContains(contact, "registrar@mavambo.test")


class StaffRegistrationTests(RegistrationBase):
    def test_request_creates_an_inactive_account_and_pending_row(self):
        response = self.client.post(
            reverse("register_staff"), self.staff_payload()
        )
        self.assertRedirects(response, reverse("register_staff_sent"))
        user = User.objects.get(username="farai")
        self.assertFalse(user.is_active)
        request = StaffRegistrationRequest.objects.get(user=user)
        self.assertEqual(request.status, StaffRegistrationRequest.Status.PENDING)
        self.assertEqual(request.position, "teacher")

    def test_position_is_limited_to_what_the_institution_accepts(self):
        response = self.client.post(
            reverse("register_staff"),
            self.staff_payload(position="nurse"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            StaffRegistrationRequest.objects.filter(
                email="farai@example.test"
            ).exists()
        )
        self.assertContains(response, "not one of the available choices")
        # The service refuses the same position even if a visitor edits the
        # form and posts it directly.
        with self.assertRaises(ValidationError):
            register_staff(
                self.institution,
                "Farai Ncube",
                "farai@example.test",
                "0771234567",
                "nurse",
                None,
                "farai",
                STRONG,
            )

    def test_administrator_role_can_never_be_requested(self):
        response = self.client.post(
            reverse("register_staff"),
            self.staff_payload(position="administrator"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            User.objects.filter(email="farai@example.test").exists()
        )
        with self.assertRaises(ValidationError):
            register_staff(
                self.institution,
                "Farai Ncube",
                "other@example.test",
                "0771234567",
                "administrator",
                None,
                "otheradmin",
                STRONG,
            )

    def test_approval_activates_the_account_with_the_right_role(self):
        self.client.post(reverse("register_staff"), self.staff_payload())
        request = StaffRegistrationRequest.objects.get(email="farai@example.test")
        self.client.force_login(self.admin)

        listing = self.client.get(reverse("registration_requests"))
        self.assertContains(listing, "Farai Ncube")
        detail = self.client.get(
            reverse("registration_request_detail", args=[request.pk])
        )
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Farai Ncube")

        response = self.client.post(
            reverse("registration_request_detail", args=[request.pk]),
            {"action": "approve", "position": "teacher", "department": ""},
        )
        self.assertEqual(response.status_code, 302)
        request.refresh_from_db()
        user = request.user
        user.refresh_from_db()
        self.assertEqual(
            request.status, StaffRegistrationRequest.Status.APPROVED
        )
        self.assertTrue(user.is_active)
        self.assertEqual(user.primary_role.code, "class_teacher")
        self.assertTrue(hasattr(user, "staff_profile"))
        self.assertEqual(user.staff_profile.job_title, "Teacher")
        self.assertTrue(self.client.login(username="farai", password=STRONG))

    def test_rejection_keeps_the_account_inactive(self):
        self.client.post(reverse("register_staff"), self.staff_payload())
        request = StaffRegistrationRequest.objects.get(email="farai@example.test")
        self.client.force_login(self.admin)
        self.client.post(
            reverse("registration_request_detail", args=[request.pk]),
            {
                "action": "reject",
                "reason": "We have no vacancy for this position at the moment.",
            },
        )
        request.refresh_from_db()
        request.user.refresh_from_db()
        self.assertEqual(
            request.status, StaffRegistrationRequest.Status.REJECTED
        )
        self.assertFalse(request.user.is_active)
        self.assertFalse(self.client.login(username="farai", password=STRONG))

    def test_new_request_sends_a_notification_to_the_institution_admin(self):
        self.client.post(reverse("register_staff"), self.staff_payload())
        from core.models import Notification

        self.assertTrue(
            Notification.all_objects.filter(
                institution=self.institution,
                user=self.admin,
                category="registration",
            ).exists()
        )

    def test_another_institution_cannot_see_or_decide_the_request(self):
        self.client.post(reverse("register_staff"), self.staff_payload())
        request = StaffRegistrationRequest.objects.get(email="farai@example.test")
        self.client.force_login(self.other_admin)

        detail = self.client.get(
            reverse("registration_request_detail", args=[request.pk])
        )
        self.assertEqual(detail.status_code, 404)
        decision = self.client.post(
            reverse("registration_request_detail", args=[request.pk]),
            {"action": "approve", "position": "teacher", "department": ""},
        )
        self.assertEqual(decision.status_code, 404)
        request.refresh_from_db()
        self.assertEqual(request.status, StaffRegistrationRequest.Status.PENDING)

        listing = self.client.get(reverse("registration_requests"))
        self.assertNotContains(listing, "Farai Ncube")

    def test_the_staff_sent_page_confirms_without_promising_approval(self):
        self.client.post(reverse("register_staff"), self.staff_payload())
        response = self.client.get(reverse("register_staff_sent"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your request has been sent")
        self.assertContains(response, "cannot sign in until")
        self.assertContains(response, escape(STAFF_GENERIC_PENDING))


class InstitutionRegistrationSettingsTests(RegistrationBase):
    def test_settings_save_the_switch_and_the_positions(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("institution_settings"),
            {
                "action": "registration",
                "public_registration": "on",
                "staff_positions": ["teacher", "librarian"],
            },
        )
        self.assertEqual(response.status_code, 302)
        self.institution.refresh_from_db()
        self.assertTrue(self.institution.public_registration)
        self.assertEqual(
            self.institution.staff_positions, ["teacher", "librarian"]
        )
