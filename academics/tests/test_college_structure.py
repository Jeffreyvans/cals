from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from academics.forms import (
    CoursePrerequisiteForm,
    ProgrammeYearForm,
    StudentRegistrationForm,
)
from academics.models import (
    AcademicYear,
    Cohort,
    Course,
    CourseOffering,
    CoursePrerequisite,
    Programme,
    ProgrammeYear,
    SchoolSettings,
    Term,
)

User = get_user_model()


class CollegeStructureTests(TestCase):
    def setUp(self):
        self.registrar = User.objects.create_user(
            "registrar", password="pw123456", role="registrar")
        self.student_user = User.objects.create_user(
            "student", password="pw123456", role="student")
        self.year = AcademicYear.objects.create(
            name="2026", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        self.term = Term.objects.create(
            academic_year=self.year, name="Term 1",
            start_date=date(2026, 1, 12), end_date=date(2026, 4, 10))
        self.programme = Programme.objects.create(
            name="Diploma in IT", code="DIT", level="diploma")
        self.other_programme = Programme.objects.create(
            name="BSc IT", code="BSC", level="degree")
        self.course = Course.objects.create(
            code="IT101", title="Programming", programme=self.programme)
        self.required = Course.objects.create(
            code="IT100", title="Foundations", programme=self.programme)

    def post(self, **data):
        return self.client.post(reverse("college_structure"), data)

    def test_registrar_can_open_the_structure_screen(self):
        self.client.force_login(self.registrar)
        response = self.client.get(reverse("college_structure"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Add a programme year")
        self.assertContains(response, "Offer a course")

    def test_student_cannot_open_the_structure_screen(self):
        self.client.force_login(self.student_user)
        response = self.client.get(reverse("college_structure"))
        self.assertEqual(response.status_code, 403)

    def test_registrar_adds_a_programme_year(self):
        self.client.force_login(self.registrar)
        response = self.post(
            action="new_programme_year", programme=self.programme.pk,
            academic_year=self.year.pk, name="Year 1", number="1",
            capacity="40", credit_limit="18",
            registration_opens="2026-01-01", registration_closes="2026-02-01")
        self.assertRedirects(response, reverse("college_structure"))
        row = ProgrammeYear.objects.get(programme=self.programme)
        self.assertEqual(row.credit_limit, 18)
        self.assertEqual(row.registration_closes, date(2026, 2, 1))

    def test_registrar_adds_a_cohort(self):
        programme_year = ProgrammeYear.objects.create(
            programme=self.programme, academic_year=self.year)
        self.client.force_login(self.registrar)
        response = self.post(
            action="new_cohort", programme_year=programme_year.pk, name="Group A")
        self.assertRedirects(response, reverse("college_structure"))
        self.assertTrue(Cohort.objects.filter(
            programme_year=programme_year, name="Group A").exists())

    def test_registrar_offers_a_course(self):
        self.client.force_login(self.registrar)
        response = self.post(
            action="new_offering", course=self.course.pk, term=self.term.pk,
            capacity="25", credits="4", enrolment_deadline="2026-01-20")
        self.assertRedirects(response, reverse("college_structure"))
        offering = CourseOffering.objects.get(course=self.course, term=self.term)
        self.assertEqual(offering.capacity, 25)
        self.assertEqual(offering.credit_points, 4)

    def test_registrar_adds_a_prerequisite(self):
        self.client.force_login(self.registrar)
        response = self.post(
            action="new_prerequisite", course=self.course.pk,
            required=self.required.pk, min_mark="50")
        self.assertRedirects(response, reverse("college_structure"))
        rule = CoursePrerequisite.objects.get(course=self.course)
        self.assertEqual(rule.required, self.required)
        self.assertEqual(str(rule.min_mark), "50.00")

    def test_programme_year_window_must_run_forwards(self):
        form = ProgrammeYearForm({
            "programme": self.programme.pk, "academic_year": self.year.pk,
            "number": "1", "capacity": "0", "credit_limit": "0",
            "registration_opens": "2026-02-01",
            "registration_closes": "2026-01-01",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("registration_closes", form.errors)

    def test_a_course_cannot_be_its_own_prerequisite(self):
        form = CoursePrerequisiteForm({
            "course": self.course.pk, "required": self.course.pk,
        })
        self.assertFalse(form.is_valid())
        self.assertIn("cannot be its own", " ".join(form.errors["__all__"]))

    def test_college_student_form_can_choose_a_programme_year_and_cohort(self):
        programme_year = ProgrammeYear.objects.create(
            programme=self.programme, academic_year=self.year)
        cohort = Cohort.objects.create(programme_year=programme_year, name="Group A")
        form = StudentRegistrationForm()
        self.assertIn("programme_year", form.fields)
        self.assertIn("cohort", form.fields)
        form = StudentRegistrationForm({
            "first_name": "Tariro", "last_name": "Dube", "username": "tariro",
            "password": "pw123456", "programme": self.programme.pk,
            "programme_year": programme_year.pk, "cohort": cohort.pk,
            "year_or_grade": "1", "guardian_name": "", "guardian_phone": "",
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["cohort"], cohort)

    def test_school_student_form_hides_college_fields(self):
        settings_row = SchoolSettings.get()
        settings_row.mode = SchoolSettings.Mode.SCHOOL
        settings_row.save(update_fields=["mode"])
        form = StudentRegistrationForm()
        self.assertNotIn("programme_year", form.fields)
        self.assertNotIn("cohort", form.fields)
