"""Part 5: configurable student and staff numbers.

An institution chooses the prefix, whether the year is part of the number, the
padding and the next number to issue. A number is only generated when one was
not supplied, so an explicit or imported number is always kept.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from academics.models import Programme, Student
from admissions.models import Application
from core.models import (
    AuditEntry,
    Institution,
    NumberFormat,
    NumberFormatChange,
    NumberSequence,
    Role,
    StaffProfile,
)
from core.numbering import (
    calculate_check_letter,
    format_number,
    formats_for,
    get_format,
    manual_override,
    next_number,
    number_key,
    peek_next,
    preview_numbers,
    save_format,
    validate_manual_number,
)
from core.roles import ensure_roles

User = get_user_model()
YEAR = timezone.localdate().year


class NumberFormatTests(TestCase):
    def setUp(self):
        self.inst = Institution.objects.create(
            name="Alpha College", slug="alpha", type="college")

    def test_defaults_are_used_when_nothing_is_stored(self):
        fmt = get_format(self.inst, "student")
        self.assertEqual(fmt["prefix"], "STU")
        self.assertTrue(fmt["include_year"])
        self.assertEqual(fmt["padding"], 4)

    def test_format_number_uses_prefix_year_and_padding(self):
        self.assertEqual(
            format_number(self.inst, "student", 7), f"STU-{YEAR}-0007")

    def test_save_format_stores_the_shape_and_seeds_the_sequence(self):
        save_format(self.inst, "student", prefix="adm", include_year=False,
                    padding=3, next_value=42)
        self.inst.refresh_from_db()
        fmt = get_format(self.inst, "student")
        self.assertEqual(fmt["prefix"], "ADM")
        self.assertFalse(fmt["include_year"])
        self.assertEqual(fmt["padding"], 3)
        self.assertEqual(peek_next(self.inst, "student"), 42)
        self.assertEqual(format_number(self.inst, "student", 42), "ADM-042")

    def test_a_bad_prefix_is_refused(self):
        with self.assertRaises(ValidationError):
            save_format(self.inst, "student", prefix="bad prefix!", include_year=True,
                        padding=4, next_value=1)

    def test_a_bad_padding_is_refused(self):
        for value in (0, 99, "abc"):
            with self.assertRaises(ValidationError):
                save_format(self.inst, "student", prefix="STU", include_year=True,
                            padding=value, next_value=1)

    def test_a_bad_next_number_is_refused(self):
        for value in (0, -5, "abc"):
            with self.assertRaises(ValidationError):
                save_format(self.inst, "student", prefix="STU", include_year=True,
                            padding=4, next_value=value)

    def test_next_number_increments_and_formats(self):
        first = next_number(self.inst, "student")
        second = next_number(self.inst, "student")
        self.assertEqual(first, f"STU-{YEAR}-0001")
        self.assertEqual(second, f"STU-{YEAR}-0002")
        self.assertEqual(peek_next(self.inst, "student"), 3)

    def test_next_number_without_the_year(self):
        save_format(self.inst, "staff", prefix="EMP", include_year=False,
                    padding=4, next_value=1)
        self.assertEqual(next_number(self.inst, "staff"), "EMP-0001")

    def test_student_and_staff_sequences_are_separate(self):
        next_number(self.inst, "student")
        next_number(self.inst, "student")
        self.assertEqual(peek_next(self.inst, "staff"), 1)
        self.assertEqual(next_number(self.inst, "staff"), f"EMP-{YEAR}-0001")

    def test_formats_for_returns_a_preview(self):
        rows = formats_for(self.inst)
        self.assertEqual(rows["student"]["next"], 1)
        self.assertEqual(rows["student"]["preview"], f"STU-{YEAR}-0001")
        self.assertIn("staff", rows)


class StudentNumberTests(TestCase):
    def setUp(self):
        self.inst = Institution.objects.get(slug="default")
        self.programme = Programme.objects.create(name="Diploma in IT", code="DIT",
                                                  level="diploma")

    def student(self, username, **kwargs):
        user = User.objects.create_user(username, password="pw123456", role="student")
        return Student.objects.create(user=user, programme=self.programme, **kwargs)

    def test_a_new_student_gets_a_generated_number(self):
        student = self.student("s1")
        self.assertEqual(student.student_no, f"STU-{YEAR}-0001")

    def test_numbers_are_sequential_and_unique(self):
        first = self.student("s1")
        second = self.student("s2")
        self.assertEqual(first.student_no, f"STU-{YEAR}-0001")
        self.assertEqual(second.student_no, f"STU-{YEAR}-0002")

    def test_an_explicit_number_is_kept(self):
        student = self.student("s1", student_no="202600001")
        self.assertEqual(student.student_no, "202600001")
        self.assertEqual(peek_next(self.inst, "student"), 1)

    def test_changing_the_format_changes_the_next_number(self):
        save_format(self.inst, "student", prefix="ADM", include_year=False,
                    padding=3, next_value=5)
        student = self.student("s1")
        self.assertEqual(student.student_no, "ADM-005")

    def test_institutions_number_independently(self):
        from core.tenancy import use_institution

        other = Institution.objects.create(name="Beta", slug="beta", type="college")
        with use_institution(other):
            other_programme = Programme.objects.create(
                name="Diploma in IT", code="DIT", level="diploma")
            user = User.objects.create_user("b1", password="pw123456", role="student",
                                            institution=other)
            other_student = Student.objects.create(
                user=user, programme=other_programme, institution=other)
        first = self.student("s1")
        self.assertEqual(first.student_no, f"STU-{YEAR}-0001")
        self.assertEqual(other_student.student_no, f"STU-{YEAR}-0001")


class StaffNumberTests(TestCase):
    def setUp(self):
        self.inst = Institution.objects.get(slug="default")

    def test_a_new_staff_profile_gets_a_generated_number(self):
        user = User.objects.create_user("t1", password="pw123456", role="lecturer")
        profile = StaffProfile.objects.create(user=user, job_title="Teacher")
        self.assertEqual(profile.employee_no, f"EMP-{YEAR}-0001")

    def test_an_explicit_staff_number_is_kept(self):
        user = User.objects.create_user("t1", password="pw123456", role="lecturer")
        profile = StaffProfile.objects.create(user=user, employee_no="EMP-100")
        self.assertEqual(profile.employee_no, "EMP-100")
        self.assertEqual(peek_next(self.inst, "staff"), 1)

    def test_updating_a_profile_does_not_change_the_number(self):
        user = User.objects.create_user("t1", password="pw123456", role="lecturer")
        profile = StaffProfile.objects.create(user=user, job_title="Teacher")
        original = profile.employee_no
        profile.job_title = "Head of Science"
        profile.save()
        self.assertEqual(profile.employee_no, original)


class NumberSettingsViewTests(TestCase):
    def setUp(self):
        self.inst = Institution.objects.create(
            name="Alpha College", slug="alpha", type="college")
        ensure_roles(self.inst)
        self.admin = User.objects.create_user(
            "alpha_admin", password="pw123456", role="admin", institution=self.inst)
        self.admin.primary_role = Role.all_objects.get(
            institution=self.inst, code="institution_admin")
        self.admin.save(update_fields=["primary_role"])

    def test_the_settings_screen_shows_the_section(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("institution_settings"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Number formats")
        self.assertContains(resp, f"STU-{YEAR}-0001")

    def test_an_admin_saves_the_student_format(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("institution_settings"), {
            "action": "numbering", "kind": "student", "prefix": "adm",
            "padding": "3", "next": "7",
        })
        self.assertEqual(resp.status_code, 302)
        self.inst.refresh_from_db()
        fmt = get_format(self.inst, "student")
        self.assertEqual(fmt["prefix"], "ADM")
        self.assertFalse(fmt["include_year"])
        self.assertEqual(peek_next(self.inst, "student"), 7)

    def test_the_change_is_audited(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("institution_settings"), {
            "action": "numbering", "kind": "staff", "prefix": "EMP",
            "include_year": "on", "padding": "4", "next": "1",
        })
        entry = AuditEntry.all_objects.filter(
            action="institution.numbering_updated", institution=self.inst).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.after["kind"], "staff")

    def test_bad_input_is_refused_with_a_message(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("institution_settings"), {
            "action": "numbering", "kind": "student", "prefix": "STU",
            "include_year": "on", "padding": "0", "next": "1",
        }, follow=True)
        self.assertContains(resp, "between 1 and 8")
        self.inst.refresh_from_db()
        self.assertEqual(get_format(self.inst, "student")["padding"], 4)

    def test_a_role_without_the_settings_permission_is_refused(self):
        user = User.objects.create_user(
            "alpha_other", password="pw123456", role="student",
            institution=self.inst)
        self.client.force_login(user)
        resp = self.client.post(reverse("institution_settings"), {
            "action": "numbering", "kind": "student", "prefix": "HACK",
            "padding": "4", "next": "1",
        })
        self.assertEqual(resp.status_code, 403)
        self.inst.refresh_from_db()
        self.assertEqual(get_format(self.inst, "student")["prefix"], "STU")


class NumberSequenceModelTests(TestCase):
    def test_one_sequence_row_per_kind(self):
        inst = Institution.objects.get(slug="default")
        next_number(inst, "student")
        next_number(inst, "student")
        row = NumberSequence.all_objects.get(institution=inst, kind="student")
        self.assertEqual(row.last_number, 2)
        self.assertFalse(
            NumberSequence.all_objects.filter(institution=inst, kind="staff").exists())


class AdvancedNumberFormatTests(TestCase):
    """The token options: codes, separators, check letters and year resets."""

    def setUp(self):
        self.inst = Institution.objects.get(slug="default")
        self.programme = Programme.objects.create(
            name="Diploma in IT", code="DIT", level="diploma")

    def test_full_format_with_programme_code_and_calculated_check_letter(self):
        save_format(
            self.inst, "student", prefix="LIT", include_year=True,
            year_digits=4, padding=7, separator="", include_programme=True,
            check_mode="calculated", next_value=1)
        number = next_number(self.inst, "student", programme_code="DIT")
        stem = f"LIT{YEAR}DIT0000001"
        self.assertEqual(number, stem + calculate_check_letter(stem))

    def test_two_digit_year(self):
        save_format(self.inst, "student", prefix="STU", include_year=True,
                    year_digits=2, padding=4, next_value=1)
        self.assertEqual(next_number(self.inst, "student"),
                         f"STU-{str(YEAR)[-2:]}-0001")

    def test_separator_can_be_a_slash(self):
        save_format(self.inst, "staff", prefix="EMP", include_year=True,
                    year_digits=4, padding=3, separator="/", next_value=1)
        self.assertEqual(next_number(self.inst, "staff"), f"EMP/{YEAR}/001")

    def test_a_fixed_final_letter(self):
        save_format(self.inst, "student", prefix="STU", include_year=False,
                    padding=4, separator="-", check_mode="fixed",
                    check_letter="c", next_value=1)
        self.assertEqual(next_number(self.inst, "student"), "STU-0001-C")

    def test_a_yearly_sequence_starts_again_in_a_new_year(self):
        save_format(self.inst, "student", prefix="STU", include_year=True,
                    year_digits=4, padding=4, reset_yearly=True, next_value=1)
        first = next_number(self.inst, "student", day=date(2025, 12, 31))
        second = next_number(self.inst, "student", day=date(2026, 1, 1))
        self.assertEqual(first, "STU-2025-0001")
        self.assertEqual(second, "STU-2026-0001")

    def test_a_continuous_sequence_never_restarts(self):
        save_format(self.inst, "student", prefix="STU", include_year=True,
                    year_digits=4, padding=4, reset_yearly=False, next_value=1)
        first = next_number(self.inst, "student", day=date(2025, 12, 31))
        second = next_number(self.inst, "student", day=date(2026, 1, 1))
        self.assertEqual(first, "STU-2025-0001")
        self.assertEqual(second, "STU-2026-0002")
        row = NumberSequence.all_objects.get(
            institution=self.inst, kind="student", year=0)
        self.assertEqual(row.last_number, 2)

    def test_the_chosen_start_number_is_used(self):
        save_format(self.inst, "student", prefix="STU", include_year=False,
                    padding=5, start_number=1000)
        self.assertEqual(peek_next(self.inst, "student"), 1000)
        self.assertEqual(next_number(self.inst, "student"), "STU-01000")

    def test_preview_does_not_consume_a_number(self):
        previews = preview_numbers(self.inst, "student", count=3)
        self.assertEqual(previews, [f"STU-{YEAR}-0001",
                                    f"STU-{YEAR}-0002",
                                    f"STU-{YEAR}-0003"])
        self.assertEqual(peek_next(self.inst, "student"), 1)

    def test_saving_a_format_writes_the_change_history(self):
        save_format(self.inst, "student", prefix="ADM", include_year=False,
                    padding=3, next_value=1, reason="New admissions year")
        change = NumberFormatChange.all_objects.get(
            institution=self.inst, kind="student")
        self.assertEqual(change.before["prefix"], "STU")
        self.assertEqual(change.after["prefix"], "ADM")
        self.assertEqual(change.reason, "New admissions year")
        self.assertEqual(
            NumberFormat.all_objects.get(
                institution=self.inst, kind="student").prefix, "ADM")


class NumberSafetyTests(TestCase):
    """Numbers are unique per institution and are never reused."""

    def setUp(self):
        self.inst = Institution.objects.get(slug="default")
        self.programme = Programme.objects.create(
            name="Diploma in IT", code="DIT", level="diploma")

    def student(self, username, **kwargs):
        user = User.objects.create_user(
            username, password="pw123456", role="student")
        return Student.objects.create(
            user=user, programme=self.programme, **kwargs)

    def test_a_deleted_number_is_never_issued_again(self):
        first = self.student("s1")
        self.assertEqual(first.student_no, f"STU-{YEAR}-0001")
        first.delete()
        second = self.student("s2")
        self.assertEqual(second.student_no, f"STU-{YEAR}-0002")

    def test_a_typed_number_is_stored_in_upper_case(self):
        student = self.student("s1", student_no="abc-7")
        self.assertEqual(student.student_no, "ABC-7")

    def test_a_manual_number_must_be_unique(self):
        self.student("s1", student_no="ABC-7")
        with self.assertRaises(ValidationError):
            validate_manual_number(self.inst, "student", "abc-7")
        self.assertEqual(
            validate_manual_number(self.inst, "student", "abc-8"), "ABC-8")

    def test_manual_override_changes_one_number_and_logs_it(self):
        student = self.student("s1", student_no="OLD-1")
        manual_override(self.inst, "student", "old-1", "new-9")
        student.refresh_from_db()
        self.assertEqual(student.student_no, "NEW-9")
        entry = AuditEntry.all_objects.filter(
            action="numbering.manual_override",
            institution=self.inst).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.after["number"], "NEW-9")

    def test_applications_use_the_applicant_sequence(self):
        save_format(self.inst, "applicant", prefix="APP", include_year=False,
                    padding=5, next_value=10)
        application = Application.objects.create(
            institution=self.inst, first_name="Tariro", last_name="Dube",
            email="tariro@example.test")
        self.assertEqual(application.reference, "APP-00010")
        self.assertTrue(application.reference.startswith("APP"))

    def test_number_key_ignores_case_spaces_and_separators(self):
        self.assertEqual(number_key(" stu / 2026 - 0001 "), "stu20260001")


class NumberingSettingsPageTests(TestCase):
    """The dedicated page previews, saves and explains the change."""

    def setUp(self):
        self.inst = Institution.objects.create(
            name="Alpha College", slug="alpha", type="college")
        ensure_roles(self.inst)
        self.admin = User.objects.create_user(
            "alpha_admin", password="pw123456", role="admin",
            institution=self.inst)
        self.admin.primary_role = Role.all_objects.get(
            institution=self.inst, code="institution_admin")
        self.admin.save(update_fields=["primary_role"])
        self.client.force_login(self.admin)

    def test_the_page_shows_a_three_number_preview_and_a_warning(self):
        response = self.client.get(reverse("numbering_settings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"STU-{YEAR}-0001")
        self.assertContains(response, f"STU-{YEAR}-0003")
        self.assertContains(response, "Existing numbers will not be renamed")
        self.assertContains(response, "Change history")

    def test_the_page_saves_every_option(self):
        response = self.client.post(reverse("numbering_settings"), {
            "action": "format", "kind": "student", "prefix": "lit",
            "include_year": "on", "year_digits": "4", "padding": "6",
            "separator": "/", "include_programme": "on",
            "check_mode": "calculated", "reset_yearly": "on",
            "start_number": "100", "next": "100",
            "reason": "Use the programme code",
        })
        self.assertEqual(response.status_code, 302)
        fmt = get_format(self.inst, "student")
        self.assertEqual(fmt["prefix"], "LIT")
        self.assertEqual(fmt["padding"], 6)
        self.assertEqual(fmt["separator"], "/")
        self.assertTrue(fmt["include_programme"])
        self.assertEqual(fmt["check_mode"], "calculated")
        self.assertEqual(peek_next(self.inst, "student"), 100)

    def test_the_page_can_override_one_number(self):
        programme = Programme.objects.create(
            name="Diploma in IT", code="DIT", level="diploma")
        user = User.objects.create_user(
            "s1", password="pw123456", role="student",
            institution=self.inst)
        student = Student.objects.create(
            user=user, programme=programme, student_no="OLD-1",
            institution=self.inst)
        response = self.client.post(reverse("numbering_settings"), {
            "action": "override", "kind": "student",
            "current": "old-1", "number": "new-7",
        })
        self.assertEqual(response.status_code, 302)
        student.refresh_from_db()
        self.assertEqual(student.student_no, "NEW-7")
