"""The administrator Users area: direct creation, invitations and safety.

Part 2B had one code path for adding people directly. These tests cover the
promises that matter most: an administrator can add staff and students, a
temporary password is only ever shown once, an invitation works once and then
expires, a role can never be raised above the actor's own, and one institution
can never reach another institution's accounts.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from academics.models import Programme, Student
from accounts.registration import register_student
from accounts.user_admin import (
    add_staff_member,
    assignable_roles,
    check_role_allowed,
    deactivate_user,
)
from core import importer
from core.models import (
    AuditEntry,
    ImportBatch,
    Institution,
    OutboundMessage,
    StaffProfile,
)
from core.roles import ensure_roles
from core.tenancy import use_institution
from finance.models import FeeItem, FeeStructure, Invoice

User = get_user_model()
STRONG = "Str0ngPassw0rd"


class UserAdminBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.inst = Institution.objects.create(
            name="Alpha College", slug="alpha", type="college")
        cls.other = Institution.objects.create(
            name="Beta College", slug="beta", type="college")
        cls.roles = ensure_roles(cls.inst)
        cls.other_roles = ensure_roles(cls.other)
        with use_institution(cls.inst):
            cls.programme = Programme.objects.create(
                code="DIT", name="Diploma in IT", level="diploma",
                tuition_fee=500)
            structure = FeeStructure.objects.create(
                code="DIT-FEES", name="DIT fees", programme=cls.programme)
            FeeItem.objects.create(
                structure=structure, name="Tuition", amount=500)

        cls.admin = User.objects.create_user(
            "alphaadmin", password=STRONG, role="admin", institution=cls.inst)
        cls.admin.primary_role = cls.roles["institution_admin"]
        cls.admin.save(update_fields=["primary_role"])

        cls.registrar = User.objects.create_user(
            "alphareg", password=STRONG, role="registrar", institution=cls.inst)
        cls.registrar.primary_role = cls.roles["registrar"]
        cls.registrar.save(update_fields=["primary_role"])

        cls.other_admin = User.objects.create_user(
            "betaadmin", password=STRONG, role="admin", institution=cls.other)
        cls.other_admin.primary_role = cls.other_roles["institution_admin"]
        cls.other_admin.save(update_fields=["primary_role"])

    def setUp(self):
        cache.clear()

    def staff_payload(self, **overrides):
        data = {
            "full_name": "Jane Doe",
            "email": "jane.doe@example.com",
            "phone": "+263771000001",
            "role": self.roles["lecturer"].pk,
            "job_title": "Lecturer",
            "staff_no": "EMP-1",
            "start_date": "2026-01-05",
            "access_mode": "invitation",
        }
        data.update(overrides)
        return data

    def student_payload(self, **overrides):
        data = {
            "first_name": "Tariro",
            "last_name": "Dube",
            "email": "tariro@example.com",
            "programme": self.programme.pk,
            "year_or_grade": "1",
            "date_of_birth": "2008-03-04",
            "guardian_name": "Mr Dube",
            "guardian_phone": "+263771000002",
            "access_mode": "invitation",
        }
        data.update(overrides)
        return data

    def login(self, user):
        self.client.force_login(user)


class AddStaffTests(UserAdminBase):
    def test_an_administrator_adds_a_staff_member_with_an_invitation(self):
        self.login(self.admin)
        response = self.client.post(reverse("user_add_staff"), self.staff_payload())
        self.assertRedirects(response, reverse("user_created"))

        account = User.objects.get(username="jane.doe")
        self.assertTrue(account.is_active)
        self.assertFalse(account.has_usable_password())
        self.assertFalse(account.must_change_password)
        self.assertEqual(account.primary_role, self.roles["lecturer"])
        profile = StaffProfile.objects.get(user=account)
        self.assertEqual(profile.employee_no, "EMP-1")
        self.assertEqual(profile.job_title, "Lecturer")

        invitation = account.invitations.get()
        self.assertTrue(invitation.is_open)
        hours = (invitation.expires_at - timezone.now()).total_seconds() / 3600
        self.assertGreater(hours, 70)
        self.assertLessEqual(hours, 72)
        notice = OutboundMessage.all_objects.get(
            institution=self.inst, related_object_id=str(invitation.pk))
        self.assertIn(str(invitation.token), notice.body)

    def test_a_temporary_password_is_shown_once_and_never_again(self):
        self.login(self.admin)
        response = self.client.post(
            reverse("user_add_staff"),
            self.staff_payload(access_mode="temporary_password"))
        self.assertRedirects(response, reverse("user_created"))

        account = User.objects.get(username="jane.doe")
        self.assertTrue(account.must_change_password)
        page = self.client.get(reverse("user_created"))
        self.assertEqual(page.status_code, 200)
        password = page.context["credentials"]["password"]
        self.assertTrue(password)
        self.assertTrue(account.check_password(password))

        again = self.client.get(reverse("user_created"))
        self.assertRedirects(again, reverse("user_list"))

    def test_a_temporary_password_forces_a_change_at_the_next_sign_in(self):
        result = add_staff_member(
            self.inst,
            full_name="Jane Doe",
            email="jane.doe@example.com",
            role=self.roles["lecturer"],
            access_mode="temporary_password",
            actor=self.admin,
        )
        self.client.logout()
        self.assertTrue(self.client.login(
            username=result.user.username, password=result.password))
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("password_change"), response["Location"])


class AddStudentTests(UserAdminBase):
    def test_an_administrator_adds_a_student_with_a_login_and_first_invoice(self):
        self.login(self.admin)
        payload = self.student_payload(
            first_invoice="on", fee_structure="")
        response = self.client.post(reverse("user_add_student"), payload)
        self.assertRedirects(response, reverse("user_created"))

        student = Student.objects.get(user__username="tariro")
        self.assertTrue(student.student_no)
        self.assertEqual(student.primary_role if hasattr(student, "primary_role") else None, None)
        self.assertEqual(student.user.role, User.Role.STUDENT)
        invoice = Invoice.objects.get(student=student)
        self.assertEqual(invoice.amount, 500)

    def test_a_student_without_an_email_falls_back_to_a_temporary_password(self):
        self.login(self.admin)
        payload = self.student_payload(
            email="", access_mode="invitation")
        response = self.client.post(reverse("user_add_student"), payload)
        self.assertRedirects(response, reverse("user_created"))
        account = User.objects.get(username="tariro")
        self.assertTrue(account.must_change_password)
        self.assertTrue(account.has_usable_password())
        page = self.client.get(reverse("user_created"))
        self.assertTrue(page.context["credentials"]["password"])

    def test_a_student_added_here_cannot_self_register_again(self):
        self.login(self.admin)
        self.client.post(reverse("user_add_student"), self.student_payload())
        student = Student.objects.get(user__username="tariro")

        result = register_student(
            self.inst,
            student.student_no,
            "Dube",
            student.date_of_birth,
            "new@example.com",
            "anothername",
            STRONG,
        )
        self.assertEqual(result["outcome"], "existing")


class InvitationTests(UserAdminBase):
    def _invited_user(self, email):
        result = add_staff_member(
            self.inst,
            full_name="Jane Doe",
            email=email,
            role=self.roles["lecturer"],
            access_mode="invitation",
            actor=self.admin,
        )
        return result.user, result.user.invitations.get()

    def test_an_invitation_link_works_once(self):
        account, invitation = self._invited_user("jane@example.com")
        response = self.client.post(
            reverse("accept_invitation", args=[invitation.token]),
            {"password": STRONG, "password_confirm": STRONG},
        )
        self.assertRedirects(response, reverse("login"))
        account.refresh_from_db()
        invitation.refresh_from_db()
        self.assertTrue(account.check_password(STRONG))
        self.assertIsNotNone(invitation.used_at)

        again = self.client.post(
            reverse("accept_invitation", args=[invitation.token]),
            {"password": "An0therStrong!", "password_confirm": "An0therStrong!"},
        )
        self.assertEqual(again.status_code, 200)
        self.assertContains(again, "already been used")

    def test_an_expired_invitation_is_refused(self):
        _account, invitation = self._invited_user("expired@example.com")
        invitation.expires_at = timezone.now() - timezone.timedelta(hours=1)
        invitation.save(update_fields=["expires_at"])
        response = self.client.post(
            reverse("accept_invitation", args=[invitation.token]),
            {"password": STRONG, "password_confirm": STRONG},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "expired")


class PermissionSafetyTests(UserAdminBase):
    def test_a_registrar_cannot_add_staff_or_an_administrator(self):
        self.login(self.registrar)
        self.assertEqual(
            self.client.get(reverse("user_add_staff")).status_code, 403)
        self.assertEqual(
            self.client.post(reverse("user_add_staff"), self.staff_payload()).status_code,
            403)
        with self.assertRaises(ValidationError):
            check_role_allowed(self.registrar, self.roles["institution_admin"])
        self.assertNotIn(
            self.roles["institution_admin"], assignable_roles(self.registrar))

    def test_a_registrar_can_add_a_student(self):
        self.login(self.registrar)
        response = self.client.post(
            reverse("user_add_student"),
            self.student_payload(access_mode="temporary_password"))
        self.assertRedirects(response, reverse("user_created"))
        self.assertTrue(Student.objects.filter(user__username="tariro").exists())

    def test_nobody_can_grant_a_role_above_their_own(self):
        allowed = assignable_roles(self.registrar)
        self.assertNotIn(self.roles["principal"], allowed)
        with self.assertRaises(ValidationError):
            check_role_allowed(self.registrar, self.roles["principal"])

    def test_staff_cannot_add_a_user_to_another_institution(self):
        with self.assertRaises(ValidationError):
            add_staff_member(
                self.other,
                full_name="Outsider",
                email="outsider@example.com",
                role=self.other_roles["lecturer"],
                actor=self.admin,
            )
        self.assertFalse(
            User.objects.filter(username="outsider").exists())

    def test_an_institution_cannot_see_or_open_another_institutions_users(self):
        outsider = User.objects.create_user(
            "beta-user", password=STRONG, role="student", institution=self.other)
        self.login(self.admin)
        listing = self.client.get(reverse("user_list"))
        self.assertEqual(listing.status_code, 200)
        self.assertNotContains(listing, outsider.username)
        self.assertEqual(
            self.client.get(reverse("user_detail", args=[outsider.pk])).status_code,
            404)


class DeactivationTests(UserAdminBase):
    def test_the_last_administrator_cannot_be_deactivated(self):
        with self.assertRaises(ValidationError):
            deactivate_user(self.admin, actor=self.registrar)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_deactivation_keeps_invoices_results_and_audit_history(self):
        from admissions.services import create_student_record

        result = create_student_record(
            self.inst,
            first_name="Tariro",
            last_name="Dube",
            email="tariro@example.com",
            programme=self.programme,
            year_or_grade="1",
            access_mode="temporary_password",
            actor=self.admin,
            first_invoice=True,
        )
        student = result["student"]
        account = result["account"].user
        deactivate_user(account, actor=self.admin)
        account.refresh_from_db()

        self.assertFalse(account.is_active)
        self.assertTrue(Student.objects.filter(pk=student.pk).exists())
        self.assertTrue(Invoice.objects.filter(student=student).exists())
        self.assertTrue(AuditEntry.all_objects.filter(
            institution=self.inst, action="user.deactivated",
            object_id=str(account.pk)).exists())


class BulkImportTests(UserAdminBase):
    def _staff_results(self, rows):
        mapping = importer.guess_mapping(
            list(rows[0].keys()), importer.STAFF_FIELDS)
        return importer.validate("staff", rows, mapping, self.inst)

    def test_the_import_reports_duplicates_unknown_roles_and_bad_dates(self):
        rows = [
            {"Username": "dup", "First Name": "A", "Last Name": "One",
             "Email": "same@example.com", "Role": "Lecturer",
             "Date Joined": "2026-01-01"},
            {"Username": "dup", "First Name": "B", "Last Name": "Two",
             "Email": "same@example.com", "Role": "Lecturer",
             "Date Joined": "not-a-date"},
            {"Username": "third", "First Name": "C", "Last Name": "Three",
             "Email": "third@example.com", "Role": "Nowhere",
             "Date Joined": "2026-01-01"},
            {"Username": "fourth", "First Name": "D", "Last Name": "Four",
             "Email": "fourth@example.com", "Role": "Student",
             "Date Joined": "2026-01-01"},
        ]
        results = self._staff_results(rows)
        problems = " ".join(
            problem for result in results for problem in result["errors"])
        self.assertIn("used twice", problems)
        self.assertIn("not a date", problems)
        self.assertIn("Unknown role", problems)
        self.assertIn("not a staff role", problems)

    def test_a_bulk_import_creates_passwords_then_forgets_them(self):
        rows = [{
            "Username": "bulkuser", "First Name": "Bulk", "Last Name": "User",
            "Email": "bulk@example.com", "Role": "Lecturer",
            "Date Joined": "2026-01-01",
        }]
        batch = ImportBatch.objects.create(
            institution=self.inst, kind="staff", filename="staff.csv")
        imported, errors = importer.run_import(
            "staff", self._staff_results(rows), self.inst, self.admin,
            access_mode="temporary_password", batch=batch)
        self.assertEqual(imported, 1)
        self.assertEqual(errors, [])
        batch.refresh_from_db()
        self.assertEqual(len(batch.credentials), 1)
        password = batch.credentials[0]["password"]
        self.assertTrue(password)
        account = User.objects.get(username="bulkuser")
        self.assertTrue(account.check_password(password))

        self.login(self.admin)
        response = self.client.get(
            reverse("import_credentials", args=[batch.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "bulkuser")
        batch.refresh_from_db()
        self.assertEqual(batch.credentials, [])
        self.assertTrue(batch.credentials_downloaded)

    def test_undo_deactivates_only_accounts_that_have_not_signed_in(self):
        rows = [{
            "Username": "bulkuser", "First Name": "Bulk", "Last Name": "User",
            "Email": "bulk@example.com", "Role": "Lecturer",
            "Date Joined": "2026-01-01",
        }]
        batch = ImportBatch.objects.create(
            institution=self.inst, kind="staff", filename="staff.csv")
        importer.run_import(
            "staff", self._staff_results(rows), self.inst, self.admin,
            access_mode="temporary_password", batch=batch)
        batch.refresh_from_db()
        account = User.objects.get(username="bulkuser")

        undone, skipped = importer.undo_import(batch, actor=self.admin)
        self.assertEqual((undone, skipped), (1, []))
        account.refresh_from_db()
        self.assertFalse(account.is_active)

        used = User.objects.create_user(
            "useduser", password=STRONG, role="student", institution=self.inst)
        batch2 = ImportBatch.objects.create(
            institution=self.inst, kind="staff", filename="second.csv",
            created_user_ids=[used.pk])
        used.last_login = timezone.now()
        used.save(update_fields=["last_login"])
        undone, skipped = importer.undo_import(batch2, actor=self.admin)
        self.assertEqual(undone, 0)
        self.assertEqual(skipped, ["useduser"])
        used.refresh_from_db()
        self.assertTrue(used.is_active)
