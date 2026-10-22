"""Phase 1: two institutions must never see each other's rows."""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from academics.models import Course, Programme, Student
from core.models import Institution, Role
from core.tenancy import use_institution
from finance.models import Invoice

User = get_user_model()


class InstitutionIsolationTests(TestCase):
    """One shared database, but each institution only ever sees its own rows."""

    @classmethod
    def setUpTestData(cls):
        cls.first = Institution.objects.create(
            name="Riverdale College", slug="riverdale", type="college")
        cls.second = Institution.objects.create(
            name="Hillcrest School", slug="hillcrest", type="secondary")

        with use_institution(cls.first):
            cls.first_programme = Programme.objects.create(
                name="Diploma in IT", code="DIT", level="diploma", tuition_fee=500)
            cls.first_student_user = User.objects.create_user(
                "r_stu", password="pw123456", role="student", institution=cls.first)
            cls.first_student = Student.objects.create(
                user=cls.first_student_user, programme=cls.first_programme,
                student_no="S0001", institution=cls.first)
            cls.first_invoice = Invoice.objects.create(
                student=cls.first_student, description="Tuition", amount=200,
                due_date=date.today(), institution=cls.first)

        with use_institution(cls.second):
            cls.second_programme = Programme.objects.create(
                name="Grade 7", code="G7", level="grade", tuition_fee=300)
            cls.second_student_user = User.objects.create_user(
                "h_stu", password="pw123456", role="student", institution=cls.second)
            cls.second_student = Student.objects.create(
                user=cls.second_student_user, programme=cls.second_programme,
                student_no="S0001", institution=cls.second)
            cls.second_invoice = Invoice.objects.create(
                student=cls.second_student, description="Tuition", amount=300,
                due_date=date.today(), institution=cls.second)

    def test_same_student_number_is_allowed_in_two_institutions(self):
        self.assertNotEqual(self.first_student.pk, self.second_student.pk)
        self.assertEqual(Student.all_objects.filter(student_no="S0001").count(), 2)

    def test_manager_reads_only_the_current_institution(self):
        with use_institution(self.first):
            self.assertEqual(Student.objects.count(), 1)
            self.assertEqual(Student.objects.first().pk, self.first_student.pk)
        with use_institution(self.second):
            self.assertEqual(Student.objects.count(), 1)
            self.assertEqual(Student.objects.first().pk, self.second_student.pk)

    def test_all_objects_bypasses_the_tenant_filter(self):
        self.assertEqual(Student.all_objects.count(), 2)
        self.assertEqual(Invoice.all_objects.count(), 2)

    def test_invoices_follow_the_student(self):
        with use_institution(self.second):
            self.assertEqual(Invoice.objects.count(), 1)
            self.assertEqual(Invoice.objects.first().pk, self.second_invoice.pk)

    def test_a_user_only_sees_their_own_institution(self):
        with use_institution(self.first):
            self.assertEqual(User.tenant_objects.count(), 1)
            self.assertEqual(User.tenant_objects.first().pk, self.first_student_user.pk)
        with use_institution(self.second):
            self.assertEqual(User.tenant_objects.count(), 1)

    def test_roles_belong_to_one_institution(self):
        from core.roles import ensure_roles

        ensure_roles(self.first)
        with use_institution(self.first):
            self.assertTrue(Role.objects.filter(institution=self.first).exists())
        with use_institution(self.second):
            self.assertFalse(Role.objects.exists())


class LazyTenantFilterTests(TestCase):
    """A queryset built early must read the institution of the query.

    Django builds the choice list of a model form when the form class is
    imported, so the tenant filter has to be applied when the query runs.
    """

    @classmethod
    def setUpTestData(cls):
        cls.first = Institution.objects.create(
            name="Riverdale College", slug="riverdale", type="college")
        cls.second = Institution.objects.create(
            name="Hillcrest School", slug="hillcrest", type="secondary")
        with use_institution(cls.first):
            Programme.objects.create(name="Diploma in IT", code="DIT", level="diploma")
        with use_institution(cls.second):
            Programme.objects.create(name="Grade 7", code="G7", level="grade")

    def test_a_queryset_built_early_reads_the_institution_at_query_time(self):
        with use_institution(self.first):
            captured = Programme.objects.all()
        with use_institution(self.second):
            self.assertEqual([p.code for p in captured], ["G7"])

    def test_counts_and_existence_also_use_the_current_institution(self):
        with use_institution(self.first):
            captured = Programme.objects.all()
            self.assertEqual(captured.count(), 1)
            self.assertTrue(captured.exists())
        with use_institution(self.second):
            self.assertEqual(captured.count(), 1)
            self.assertTrue(captured.exists())

    def test_a_model_form_built_at_import_time_sees_the_right_programmes(self):
        from academics.forms import StudentRegistrationForm

        with use_institution(self.first):
            form = StudentRegistrationForm()
            self.assertEqual(
                [p.code for p in form.fields["programme"].queryset], ["DIT"])
        with use_institution(self.second):
            form = StudentRegistrationForm()
            self.assertEqual(
                [p.code for p in form.fields["programme"].queryset], ["G7"])


class CrossInstitutionAccessTests(TestCase):
    """A signed-in user must not reach another institution's records."""

    @classmethod
    def setUpTestData(cls):
        cls.first = Institution.objects.create(
            name="Riverdale College", slug="riverdale", type="college")
        cls.second = Institution.objects.create(
            name="Hillcrest School", slug="hillcrest", type="secondary")

        with use_institution(cls.first):
            cls.admin = User.objects.create_user(
                "river_admin", password="pw123456", role="admin",
                is_staff=True, is_superuser=True, institution=cls.first)
            cls.programme = Programme.objects.create(
                name="Diploma in IT", code="DIT", level="diploma", tuition_fee=500)
            cls.student_user = User.objects.create_user(
                "river_stu", password="pw123456", role="student", institution=cls.first)
            cls.student = Student.objects.create(
                user=cls.student_user, programme=cls.programme,
                student_no="S0001", institution=cls.first)

        with use_institution(cls.second):
            cls.other_admin = User.objects.create_user(
                "hill_admin", password="pw123456", role="admin",
                is_staff=True, is_superuser=True, institution=cls.second)
            cls.other_programme = Programme.objects.create(
                name="Grade 7", code="G7", level="grade", tuition_fee=300)
            cls.other_student_user = User.objects.create_user(
                "hill_stu", password="pw123456", role="student", institution=cls.second)
            cls.other_student = Student.objects.create(
                user=cls.other_student_user, programme=cls.other_programme,
                student_no="S0001", institution=cls.second)

    def test_a_student_cannot_open_another_institutions_student(self):
        self.client.force_login(self.student_user)
        resp = self.client.get(reverse("student_detail", args=[self.other_student.pk]))
        self.assertIn(resp.status_code, (403, 404))

    def test_a_student_search_only_returns_own_institution(self):
        self.client.force_login(self.student_user)
        resp = self.client.get(reverse("student_search_api"), {"q": "S0001"})
        self.assertEqual(resp.status_code, 403)

    def test_a_student_portal_shows_only_own_records(self):
        self.client.force_login(self.student_user)
        resp = self.client.get(reverse("student_portal"))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertIn("S0001", content)
        self.assertNotIn(self.other_student.name, content)

    def test_the_middleware_sets_the_request_institution(self):
        self.client.force_login(self.other_admin)
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.wsgi_request.institution.pk, self.second.pk)

    def test_unauthenticated_users_are_sent_to_login(self):
        resp = self.client.get(reverse("institution_settings"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("login"), resp["Location"])


class UniquePerInstitutionTests(TestCase):
    """Numbers that identify a record are unique inside an institution only."""

    @classmethod
    def setUpTestData(cls):
        cls.first = Institution.objects.create(
            name="Riverdale College", slug="riverdale", type="college")
        cls.second = Institution.objects.create(
            name="Hillcrest School", slug="hillcrest", type="secondary")

    def test_the_same_course_code_is_allowed_in_two_institutions(self):
        with use_institution(self.first):
            Programme.objects.create(name="Diploma in IT", code="DIT", level="diploma")
            Course.objects.create(code="IT101", title="Programming",
                                  programme=Programme.objects.get(code="DIT"))
        with use_institution(self.second):
            Programme.objects.create(name="Grade 7", code="G7", level="grade")
            Course.objects.create(code="IT101", title="Programming",
                                  programme=Programme.objects.get(code="G7"))
        self.assertEqual(Course.all_objects.filter(code="IT101").count(), 2)

    def test_the_same_course_code_is_refused_inside_one_institution(self):
        from django.db.utils import IntegrityError

        with use_institution(self.first):
            Programme.objects.create(name="Diploma in IT", code="DIT", level="diploma")
            Course.objects.create(code="IT101", title="Programming",
                                  programme=Programme.objects.get(code="DIT"))
            with self.assertRaises(IntegrityError):
                Course.objects.create(code="IT101", title="Networking",
                                      programme=Programme.objects.get(code="DIT"))
