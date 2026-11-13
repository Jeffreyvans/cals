from datetime import date, time

from django.test import TestCase
from django.urls import reverse

from academics.documents import build_timetable_pdf
from academics.models import (
    AcademicYear,
    Course,
    Programme,
    SchoolClass,
    Student,
    Subject,
    Term,
    TimetableEntry,
)
from accounts.models import User
from core.models import Institution


class TimetablePdfTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.registrar = User.objects.create_user(
            "regpdf", password="pw123456", role="registrar")
        cls.lecturer = User.objects.create_user(
            "lecpdf", password="pw123456", role="lecturer",
            first_name="Lena", last_name="Lecturer")
        cls.bursar = User.objects.create_user(
            "burpdf", password="pw123456", role="bursar")
        cls.student = Student.objects.create(
            user=User.objects.create_user("stupdf", password="pw123456", role="student"),
            programme=Programme.objects.create(
                name="College", code="CLGP", level="college"))
        cls.year = AcademicYear.objects.create(
            name="2026", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
            is_current=True)
        cls.term = Term.objects.create(
            academic_year=cls.year, name="Term 1", start_date=date(2026, 1, 12),
            end_date=date(2026, 4, 10), is_current=True)
        cls.g3 = SchoolClass.objects.create(name="Grade 3", level="primary")
        cls.mth = Subject.objects.create(code="MTHP", name="Mathematics", level="primary")
        cls.course = Course.objects.create(
            code="ITP1", title="Programming", programme=cls.student.programme,
            lecturer=cls.lecturer)
        cls.lesson = TimetableEntry.objects.create(
            academic_year=cls.year, term=cls.term, day=1, start_time=time(8, 0),
            end_time=time(8, 45), subject=cls.mth, school_class=cls.g3,
            lecturer=cls.lecturer)
        cls.student.school_class = cls.g3
        cls.student.save(update_fields=["school_class"])

    def assert_pdf(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertIn("timetable.pdf", response["Content-Disposition"])

    def test_the_registrar_downloads_the_weekly_timetable(self):
        self.client.force_login(self.registrar)
        self.assert_pdf(self.client.get(reverse("timetable_pdf")))

    def test_the_registrar_can_filter_by_class_and_teacher(self):
        self.client.force_login(self.registrar)
        base = reverse("timetable_pdf")
        self.assert_pdf(self.client.get(f"{base}?class={self.g3.pk}"))
        self.assert_pdf(self.client.get(f"{base}?lecturer={self.lecturer.pk}"))
        self.assert_pdf(self.client.get(f"{base}?class=999999&lecturer=999999"))

    def test_a_lecturer_downloads_their_own_timetable(self):
        self.client.force_login(self.lecturer)
        self.assert_pdf(self.client.get(reverse("my_timetable_pdf")))

    def test_a_student_downloads_their_own_timetable(self):
        self.client.force_login(self.student.user)
        self.assert_pdf(self.client.get(reverse("my_student_timetable_pdf")))

    def test_an_empty_timetable_still_produces_a_pdf(self):
        TimetableEntry.objects.all().delete()
        self.client.force_login(self.registrar)
        self.assert_pdf(self.client.get(reverse("timetable_pdf")))

    def test_the_builder_renders_a_timetable_without_http(self):
        institution = Institution.objects.get(pk=TimetableEntry.objects.first().institution_id)
        entries = list(TimetableEntry.objects.all())
        pdf = build_timetable_pdf(institution, "Timetable", entries, self.term)
        self.assertTrue(pdf.startswith(b"%PDF"))
        empty = build_timetable_pdf(institution, "Timetable", [], self.term)
        self.assertTrue(empty.startswith(b"%PDF"))

    def test_only_the_owner_roles_may_download(self):
        self.client.force_login(self.bursar)
        self.assertEqual(self.client.get(reverse("timetable_pdf")).status_code, 403)
        self.assertEqual(self.client.get(reverse("my_timetable_pdf")).status_code, 403)
        self.client.force_login(self.lecturer)
        self.assertEqual(self.client.get(reverse("timetable_pdf")).status_code, 403)
        self.client.force_login(self.student.user)
        self.assertEqual(self.client.get(reverse("my_timetable_pdf")).status_code, 403)
        self.assertEqual(self.client.get(reverse("timetable_pdf")).status_code, 403)
