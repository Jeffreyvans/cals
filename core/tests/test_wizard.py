"""Phase 1: the setup wizard builds a complete institution in six steps."""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from academics.models import AcademicYear, GradeBand, Student, Subject, Term
from core.models import AuditEntry, Institution, InstitutionModule, Role
from core.tenancy import use_institution

User = get_user_model()


def _details(**overrides):
    data = {
        "name": "Mhofu Primary School",
        "slug": "mhofu",
        "type": "primary",
        "currency": "$",
        "timezone": "Africa/Harare",
        "language": "en",
        "address": "Box 42, Mhofu",
        "contact_email": "office@mhofu.test",
        "contact_phone": "+263 77 000 0001",
        "primary_color": "#12403c",
        "accent_color": "#e8a317",
    }
    data.update(overrides)
    return data


def _year(**overrides):
    data = {
        "year_name": "2026",
        "year_start": "2026-01-15",
        "year_end": "2026-12-10",
        "term_name": "Term 1",
        "term_start": "2026-01-15",
        "term_end": "2026-04-10",
    }
    data.update(overrides)
    return data


def _admin(**overrides):
    data = {
        "username": "mhofu_admin",
        "first_name": "Tatenda",
        "last_name": "Moyo",
        "email": "tatenda@mhofu.test",
        "password": "school12345",
        "password_confirm": "school12345",
    }
    data.update(overrides)
    return data


class WizardAccessTests(TestCase):
    """Only a platform administrator may run the wizard."""

    @classmethod
    def setUpTestData(cls):
        cls.platform = User.objects.create_user(
            "platform", password="pw123456", role="admin",
            is_staff=True, is_superuser=True)
        cls.school_admin = User.objects.create_user(
            "school_admin", password="pw123456", role="admin", is_staff=True)
        cls.teacher = User.objects.create_user(
            "teacher", password="pw123456", role="lecturer")

    def test_the_wizard_is_closed_to_visitors(self):
        resp = self.client.get(reverse("setup_wizard"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("login"), resp["Location"])

    def test_the_wizard_is_closed_to_a_teacher(self):
        self.client.force_login(self.teacher)
        resp = self.client.get(reverse("setup_wizard"))
        self.assertEqual(resp.status_code, 403)

    def test_a_school_admin_cannot_create_a_second_institution(self):
        self.client.force_login(self.school_admin)
        resp = self.client.get(reverse("setup_wizard"))
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Institution.objects.count(), 1)

    def test_the_platform_admin_may_open_the_wizard(self):
        self.client.force_login(self.platform)
        resp = self.client.get(reverse("setup_wizard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Set up a new institution")

    def test_the_school_admin_has_no_permission_to_create_institutions(self):
        from core.access import user_has_code

        self.assertFalse(user_has_code(self.school_admin, "admin.create_institutions"))


class WizardStepTests(TestCase):
    """Each step keeps the answers and moves on to the next one."""

    @classmethod
    def setUpTestData(cls):
        cls.platform = User.objects.create_user(
            "platform", password="pw123456", role="admin",
            is_staff=True, is_superuser=True)

    def setUp(self):
        self.client.force_login(self.platform)

    def _walk_to(self, step):
        """Fill in every step before the one named."""
        if step == "details":
            return
        self.client.post(reverse("setup_wizard"), _details())
        if step == "year":
            return
        self.client.post(reverse("setup_wizard"), _year())
        if step == "modules":
            return
        self.client.post(reverse("setup_wizard"), {"modules": ["core_academics"]})
        if step == "grading":
            return
        self.client.post(reverse("setup_wizard"), {
            "letter": ["A"], "min_mark": ["75"], "points": ["4"]})

    def test_the_first_step_asks_for_the_details(self):
        resp = self.client.get(reverse("setup_wizard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Institution name")
        self.assertContains(resp, "Kind of institution")

    def test_the_steps_are_walked_one_at_a_time(self):
        resp = self.client.post(reverse("setup_wizard"), _details())
        self.assertEqual(resp.status_code, 302)
        resp = self.client.get(reverse("setup_wizard"))
        self.assertContains(resp, "Academic year name")

        resp = self.client.post(reverse("setup_wizard"), _year())
        self.assertEqual(resp.status_code, 302)
        resp = self.client.get(reverse("setup_wizard"))
        self.assertContains(resp, "Finance and fees")

        resp = self.client.post(reverse("setup_wizard"), {"modules": ["finance"]})
        self.assertEqual(resp.status_code, 302)
        resp = self.client.get(reverse("setup_wizard"))
        self.assertContains(resp, "Lowest mark")

        resp = self.client.post(reverse("setup_wizard"), {
            "letter": ["A", "B"], "min_mark": ["75", "65"], "points": ["4", "3"]})
        self.assertEqual(resp.status_code, 302)
        resp = self.client.get(reverse("setup_wizard"))
        self.assertContains(resp, "Username")

        resp = self.client.post(reverse("setup_wizard"), _admin())
        self.assertEqual(resp.status_code, 302)
        resp = self.client.get(reverse("setup_wizard"))
        self.assertContains(resp, "demo data")

    def test_going_back_keeps_the_answers(self):
        self.client.post(reverse("setup_wizard"), _details())
        self.client.post(reverse("setup_wizard"), _year())
        resp = self.client.post(reverse("setup_wizard"), {"action": "back"})
        self.assertEqual(resp.status_code, 302)
        resp = self.client.get(reverse("setup_wizard"))
        self.assertContains(resp, "Academic year name")
        self.assertContains(resp, "2026")

    def test_a_bad_web_name_is_refused_with_a_message(self):
        resp = self.client.post(reverse("setup_wizard"), _details(slug="not a slug"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "small letters, numbers and dashes")

    def test_a_web_name_already_in_use_is_refused(self):
        Institution.objects.create(name="Taken", slug="taken", type="primary")
        resp = self.client.post(reverse("setup_wizard"), _details(slug="taken"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "already taken")

    def test_a_year_that_ends_before_it_starts_is_refused(self):
        self._walk_to("year")
        resp = self.client.post(reverse("setup_wizard"),
                                _year(year_end="2026-01-01"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "must end after it starts")

    def test_two_different_passwords_are_refused(self):
        self._walk_to("admin")
        resp = self.client.post(reverse("setup_wizard"),
                                _admin(password_confirm="different"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "not the same")

    def test_a_short_password_is_refused(self):
        self._walk_to("admin")
        resp = self.client.post(reverse("setup_wizard"),
                                _admin(password="abc", password_confirm="abc"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "at least 8 characters")

    def test_a_grade_band_with_the_same_letter_twice_is_refused(self):
        self._walk_to("grading")
        resp = self.client.post(reverse("setup_wizard"), {
            "letter": ["A", "A"], "min_mark": ["75", "65"], "points": ["4", "3"]})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "listed twice")


class WizardBuildTests(TestCase):
    """The last step writes everything the school needs."""

    @classmethod
    def setUpTestData(cls):
        cls.platform = User.objects.create_user(
            "platform", password="pw123456", role="admin",
            is_staff=True, is_superuser=True)

    def setUp(self):
        self.client.force_login(self.platform)

    def _run_to_the_end(self, demo=False):
        self.client.post(reverse("setup_wizard"), _details())
        self.client.post(reverse("setup_wizard"), _year())
        self.client.post(reverse("setup_wizard"),
                         {"modules": ["core_academics", "finance", "library"]})
        self.client.post(reverse("setup_wizard"), {
            "letter": ["A", "B", "C"], "min_mark": ["75", "65", "50"],
            "points": ["4", "3", "2"]})
        self.client.post(reverse("setup_wizard"), _admin())
        payload = {"enabled": "on"} if demo else {}
        payload["prefix"] = "demo_" if demo else ""
        return self.client.post(reverse("setup_wizard"), payload)

    def test_the_institution_is_created_with_everything_it_asked_for(self):
        resp = self._run_to_the_end()
        self.assertRedirects(resp, reverse("setup_done"))
        institution = Institution.objects.get(slug="mhofu")
        self.assertEqual(institution.name, "Mhofu Primary School")
        self.assertEqual(institution.type, "primary")
        self.assertEqual(institution.currency, "$")

        with use_institution(institution):
            year = AcademicYear.objects.get()
            self.assertEqual(year.name, "2026")
            self.assertTrue(year.is_current)
            term = Term.objects.get()
            self.assertEqual(term.name, "Term 1")
            self.assertTrue(term.is_current)
            self.assertEqual(
                sorted(GradeBand.objects.values_list("letter", flat=True)),
                ["A", "B", "C"])
            self.assertEqual(
                GradeBand.objects.get(letter="A").min_mark, 75)

    def test_only_the_chosen_modules_are_switched_on(self):
        self._run_to_the_end()
        institution = Institution.objects.get(slug="mhofu")
        with use_institution(institution):
            on = {
                row.code for row in
                InstitutionModule.objects.filter(institution=institution, enabled=True)
            }
            off = {
                row.code for row in
                InstitutionModule.objects.filter(institution=institution, enabled=False)
            }
        self.assertEqual(on, {"core_academics", "finance", "library"})
        self.assertIn("lms", off)

    def test_the_first_administrator_gets_the_institution_admin_role(self):
        self._run_to_the_end()
        institution = Institution.objects.get(slug="mhofu")
        admin = User.objects.get(username="mhofu_admin")
        self.assertEqual(admin.institution_id, institution.pk)
        self.assertEqual(admin.primary_role.code, "institution_admin")
        self.assertTrue(admin.has_code("admin.manage_settings"))
        self.assertTrue(admin.check_password("school12345"))

    def test_the_new_institution_gets_its_own_roles(self):
        self._run_to_the_end()
        institution = Institution.objects.get(slug="mhofu")
        with use_institution(institution):
            self.assertTrue(Role.objects.filter(code="registrar").exists())
            self.assertTrue(Role.objects.filter(code="bursar").exists())

    def test_the_institution_admin_does_not_get_the_platform_permission(self):
        self._run_to_the_end()
        admin = User.objects.get(username="mhofu_admin")
        self.assertFalse(admin.has_code("admin.create_institutions"))

    def test_the_creation_is_written_to_the_audit_log(self):
        self._run_to_the_end()
        institution = Institution.objects.get(slug="mhofu")
        entry = AuditEntry.all_objects.filter(
            action="institution.created", institution=institution).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.model_name, "Institution")

    def test_the_wizard_can_add_demo_data(self):
        self._run_to_the_end(demo=True)
        institution = Institution.objects.get(slug="mhofu")
        with use_institution(institution):
            self.assertGreater(Student.objects.count(), 0)
            self.assertGreater(Subject.objects.count(), 0)
            self.assertTrue(User.objects.filter(username="demo_student1").exists())
        self.assertTrue(institution.settings["demo_data"])

    def test_the_demo_uses_the_year_the_wizard_made(self):
        self._run_to_the_end(demo=True)
        institution = Institution.objects.get(slug="mhofu")
        with use_institution(institution):
            self.assertEqual(AcademicYear.objects.count(), 1)
            self.assertEqual(Term.objects.count(), 1)

    def test_the_done_page_shows_the_demo_logins(self):
        self._run_to_the_end(demo=True)
        resp = self.client.get(reverse("setup_done"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "demo_student1")
        self.assertContains(resp, "demo1234")

    def test_a_second_institution_is_kept_apart_from_the_first(self):
        self._run_to_the_end()
        self.client.post(reverse("setup_wizard"),
                         _details(name="Chiredzi High", slug="chiredzi", type="secondary"))
        self.client.post(reverse("setup_wizard"), _year())
        self.client.post(reverse("setup_wizard"), {"modules": ["core_academics"]})
        self.client.post(reverse("setup_wizard"), {
            "letter": ["A"], "min_mark": ["75"], "points": ["4"]})
        self.client.post(reverse("setup_wizard"),
                         _admin(username="chiredzi_admin", password="school12345",
                                password_confirm="school12345"))
        self.client.post(reverse("setup_wizard"), {"prefix": ""})

        self.assertEqual(Institution.objects.count(), 3)
        with use_institution(Institution.objects.get(slug="mhofu")):
            self.assertEqual(Student.objects.count(), 0)
        with use_institution(Institution.objects.get(slug="chiredzi")):
            self.assertEqual(Student.objects.count(), 0)
        self.assertEqual(User.objects.filter(username="mhofu_admin").count(), 1)
        self.assertEqual(User.objects.filter(username="chiredzi_admin").count(), 1)


class BrandingTests(TestCase):
    """The institution's colours must stay readable."""

    def test_white_text_is_kept_on_a_dark_colour(self):
        from core.branding import readable_text

        self.assertEqual(readable_text("#12403c"), "#ffffff")

    def test_dark_text_is_used_on_a_light_colour(self):
        from core.branding import readable_text

        self.assertEqual(readable_text("#f4d03f"), "#1d2b2a")

    def test_the_contrast_ratio_is_measured_correctly(self):
        from core.branding import contrast_ratio

        self.assertAlmostEqual(contrast_ratio("#000000", "#ffffff"), 21.0, places=2)
        self.assertAlmostEqual(contrast_ratio("#ffffff", "#ffffff"), 1.0, places=2)

    def test_a_colour_that_is_not_a_colour_falls_back_to_the_default(self):
        from core.branding import accent_colour, primary_colour

        institution = Institution(name="Test", slug="test", type="primary",
                                   primary_color="not a colour",
                                   accent_color="#00ff00")
        self.assertEqual(primary_colour(institution), "#12403c")
        self.assertEqual(accent_colour(institution), "#00ff00")

    def test_the_branding_reaches_the_page(self):
        institution = Institution.objects.create(
            name="Blue School", slug="blue", type="primary",
            primary_color="#0b3d91")
        admin = User.objects.create_user(
            "blue_admin", password="pw123456", role="admin",
            is_staff=True, is_superuser=True, institution=institution)
        self.client.force_login(admin)
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "--side:#0b3d91")


class WizardDateTests(TestCase):
    """The wizard refuses dates that cannot work."""

    def test_a_term_outside_the_academic_year_is_refused(self):
        from core.wizard import clean_year

        _clean, errors = clean_year(_year(term_start="2025-12-01"))
        self.assertTrue(errors)

    def test_a_valid_year_has_no_problems(self):
        from core.wizard import clean_year

        clean, errors = clean_year(_year())
        self.assertEqual(errors, [])
        self.assertEqual(clean["year_name"], "2026")

    def test_the_wizard_defaults_to_a_sane_term(self):
        from core.wizard import clean_year

        clean, _errors = clean_year({"year_name": "2026", "year_start": "2026-01-01",
                                     "year_end": "2026-12-31"})
        self.assertEqual(clean["term_name"], "Term 1")


class WizardTodayTests(TestCase):
    """The dates the wizard writes are real dates."""

    def test_the_year_and_term_are_saved_as_dates(self):
        from core.wizard import clean_year

        clean, _errors = clean_year(_year())
        self.assertEqual(date.fromisoformat(clean["year_start"]),
                         date(2026, 1, 15))
