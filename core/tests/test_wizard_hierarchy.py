"""The setup wizard's structure step: ticks, renames and additions."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from academics.hierarchy import template_for
from academics.models import AcademicSchool, GradeLevel, Term
from core.models import Department, Institution
from core.tenancy import use_institution

from .test_wizard import _academic_type, _admin, _details, _structure, _year

User = get_user_model()


class WizardStructureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.platform = User.objects.create_user(
            "platform_hierarchy", password="pw123456", role="admin",
            is_staff=True, is_superuser=True)

    def setUp(self):
        self.client.force_login(self.platform)

    def _run(self, institution_type, structure, *, level_set=None, term_scheme=None,
             slug="wizard-hierarchy"):
        details = _details(type=institution_type, slug=slug,
                           name=f"Wizard {institution_type}")
        academic = _academic_type(institution_type, level_set=level_set)
        if term_scheme:
            academic["term_scheme"] = term_scheme
        self.client.post(reverse("setup_wizard"), details)
        self.client.post(reverse("setup_wizard"), academic)
        self.client.post(reverse("setup_wizard"), structure)
        self.client.post(reverse("setup_wizard"), _year(
            term_name="Semester 1" if academic["term_scheme"] == "semesters" else "Term 1"))
        self.client.post(reverse("setup_wizard"), {"modules": ["core_academics"]})
        self.client.post(reverse("setup_wizard"), {
            "letter": ["A"], "min_mark": ["75"], "points": ["4"]})
        self.client.post(reverse("setup_wizard"), _admin(
            username=f"{slug}_admin", password="school12345", password_confirm="school12345"))
        response = self.client.post(reverse("setup_wizard"), {"prefix": ""})
        self.assertRedirects(response, reverse("setup_done"))
        return Institution.objects.get(slug=slug)

    def test_ticks_renames_and_additions_are_copied_into_the_institution(self):
        template = template_for("university", level_set="college")
        first_school = template["schools"][0]
        first_department = first_school["departments"][0]
        first_level = template["levels"][0]

        structure = {
            "schools": [first_school["code"]],
            "departments": [first_department["code"]],
            "levels": [first_level["code"]],
            f"schools_name_{first_school['code']}": "Faculty of the Future",
            f"departments_name_{first_department['code']}": "Quantum Studies",
            f"levels_name_{first_level['code']}": "Foundation Year",
            "new_school_name": "Custom Faculty",
            "new_school_code": "CUS",
            "new_department_name": "Custom Department",
            "new_department_code": "CDEP",
            "new_department_school": first_school["code"],
            "new_level_name": "Year 5",
            "new_level_code": "Y5",
        }
        institution = self._run("university", structure, slug="wizard-university")

        with use_institution(institution):
            schools = list(AcademicSchool.objects.order_by("name"))
            self.assertEqual([row.name for row in schools],
                             ["Custom Faculty", "Faculty of the Future"])
            faculty = AcademicSchool.objects.get(name="Faculty of the Future")
            self.assertTrue(Department.objects.filter(
                name="Quantum Studies", school=faculty).exists())
            custom = Department.objects.get(name="Custom Department")
            self.assertEqual(custom.school, faculty)
            level_names = set(GradeLevel.objects.values_list("name", flat=True))
            self.assertEqual(level_names, {"Foundation Year", "Year 5"})
            # The wizard's own first term plus the two semester defaults.
            self.assertEqual(set(Term.objects.values_list("name", flat=True)),
                             {"Semester 1", "Semester 2"})
            self.assertEqual(institution.settings["academic_template"]["term_scheme"],
                             "semesters")

    def test_a_primary_school_hides_schools_and_keeps_one_term(self):
        self.client.post(reverse("setup_wizard"), _details(type="primary", slug="wizard-primary"))
        self.client.post(reverse("setup_wizard"), _academic_type("primary"))
        response = self.client.get(reverse("setup_wizard"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Schools or faculties")
        self.assertContains(response, "Template version")

        self.client.post(reverse("setup_wizard"), _structure("primary"))
        self.client.post(reverse("setup_wizard"), _year())
        self.client.post(reverse("setup_wizard"), {"modules": ["core_academics"]})
        self.client.post(reverse("setup_wizard"), {
            "letter": ["A"], "min_mark": ["75"], "points": ["4"]})
        self.client.post(reverse("setup_wizard"), _admin(
            username="wizard_primary_admin", password="school12345",
            password_confirm="school12345"))
        response = self.client.post(reverse("setup_wizard"), {"prefix": ""})
        self.assertRedirects(response, reverse("setup_done"))
        institution = Institution.objects.get(slug="wizard-primary")
        with use_institution(institution):
            self.assertFalse(AcademicSchool.objects.exists())
            self.assertEqual([row.name for row in Term.objects.all()], ["Term 1"])

    def test_the_selected_template_version_is_recorded(self):
        institution = self._run("secondary", _structure("secondary"), slug="wizard-secondary")
        applied = institution.settings["academic_template"]
        self.assertEqual(applied["level_set"], "secondary")
        self.assertEqual(applied["term_scheme"], "terms")
        self.assertIn("version", applied)
