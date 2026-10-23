from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from academics.models import (
    AcademicYear,
    Course,
    Enrollment,
    GradeBand,
    Programme,
    Student,
    Subject,
    Term,
)
from accounts.models import User


class GradeBandTests(TestCase):
    def test_default_scale_when_no_bands_are_saved(self):
        self.assertEqual(GradeBand.grade_for(Decimal("75"))[0], "A")
        self.assertEqual(GradeBand.grade_for(Decimal("74.99"))[0], "B")
        self.assertEqual(GradeBand.grade_for(Decimal("65"))[0], "B")
        self.assertEqual(GradeBand.grade_for(Decimal("50"))[0], "C")
        self.assertEqual(GradeBand.grade_for(Decimal("40"))[0], "D")
        self.assertEqual(GradeBand.grade_for(Decimal("39.99"))[0], "F")
        self.assertEqual(GradeBand.grade_for(None)[0], "—")

    def test_custom_bands_replace_the_default_scale(self):
        for letter, mark, pts in [("A", 90, 4), ("B", 80, 3), ("C", 70, 2),
                                  ("D", 60, 1), ("F", 0, 0)]:
            GradeBand.objects.create(letter=letter, min_mark=mark, points=pts)
        self.assertEqual(GradeBand.grade_for(Decimal("95"))[0], "A")
        self.assertEqual(GradeBand.grade_for(Decimal("85"))[0], "B")
        self.assertEqual(GradeBand.grade_for(Decimal("72"))[0], "C")
        self.assertEqual(GradeBand.grade_for(Decimal("60"))[0], "D")
        self.assertEqual(GradeBand.grade_for(Decimal("59"))[0], "F")

    def test_duplicate_letter_is_refused_even_with_different_case(self):
        GradeBand.objects.create(letter="A", min_mark=75, points=4)
        with self.assertRaises(ValidationError):
            GradeBand.objects.create(letter="a", min_mark=80, points=4)

    def test_min_mark_outside_zero_to_hundred_is_refused(self):
        with self.assertRaises(ValidationError):
            GradeBand.objects.create(letter="A", min_mark=150, points=4)
        with self.assertRaises(ValidationError):
            GradeBand.objects.create(letter="A", min_mark=-5, points=4)


class BaseAssessment(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.lecturer = User.objects.create_user("lec", password="pw123456",
                                                first_name="Linda", role="lecturer")
        cls.other = User.objects.create_user("lec2", password="pw123456",
                                             first_name="Oscar", role="lecturer")
        cls.registrar = User.objects.create_user("reg", password="pw123456", role="registrar")
        cls.bursar = User.objects.create_user("bur", password="pw123456", role="bursar")
        cls.admin = User.objects.create_user("root", password="pw123456", role="admin")
        cls.programme = Programme.objects.create(name="Diploma in IT", code="DIT",
                                                 level="diploma")
        cls.course = Course.objects.create(code="IT101", title="Programming",
                                           programme=cls.programme, lecturer=cls.lecturer)
        ua = User.objects.create_user("alice", password="pw123456", first_name="Alice",
                                      last_name="Ashaw", role="student")
        ub = User.objects.create_user("bob", password="pw123456", first_name="Bob",
                                      last_name="Banda", role="student")
        cls.a = Student.objects.create(user=ua, programme=cls.programme)
        cls.b = Student.objects.create(user=ub, programme=cls.programme)
        year = AcademicYear.objects.create(
            name="2026", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        cls.term = Term.objects.create(
            academic_year=year, name="Term 1", start_date=date(2026, 1, 12),
            end_date=date(2026, 4, 10))
        cls.e_a = Enrollment.objects.create(student=cls.a, course=cls.course,
                                            term=cls.term)
        cls.e_b = Enrollment.objects.create(student=cls.b, course=cls.course,
                                            term=cls.term)
        cls.subject = Subject.objects.create(code="MTH", name="Mathematics",
                                             level="secondary", lecturer=cls.lecturer)
        cls.sa = Enrollment.objects.create(student=cls.a, subject=cls.subject,
                                           term=cls.term)


class GradeUsesBandsTests(BaseAssessment):
    def test_enrollment_grade_uses_saved_bands(self):
        GradeBand.objects.create(letter="P", min_mark=50, points=1)
        GradeBand.objects.create(letter="F", min_mark=0, points=0)
        self.e_a.mark = Decimal("55")
        self.assertEqual(self.e_a.grade, "P")
        self.e_b.mark = Decimal("49")
        self.assertEqual(self.e_b.grade, "F")

    def test_enrollment_points_come_from_bands(self):
        GradeBand.objects.create(letter="A", min_mark=75, points=4)
        GradeBand.objects.create(letter="F", min_mark=0, points=0)
        self.e_a.mark = Decimal("80")
        self.assertEqual(self.e_a.points, Decimal("4"))


class SubjectRosterTests(BaseAssessment):
    def url(self):
        return reverse("subject_roster", args=[self.subject.pk])

    def test_lecturer_saves_marks_on_own_subject(self):
        self.client.force_login(self.lecturer)
        self.client.post(self.url(), {f"mark_{self.sa.pk}": "64.5"})
        self.sa.refresh_from_db()
        self.assertEqual(self.sa.mark, Decimal("64.5"))

    def test_other_lecturer_cannot_open_or_save(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        resp = self.client.post(self.url(), {f"mark_{self.sa.pk}": "80"})
        self.assertEqual(resp.status_code, 403)
        self.sa.refresh_from_db()
        self.assertIsNone(self.sa.mark)

    def test_registrar_can_view_but_not_save(self):
        self.client.force_login(self.registrar)
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        resp = self.client.post(self.url(), {f"mark_{self.sa.pk}": "80"})
        self.assertEqual(resp.status_code, 403)
        self.sa.refresh_from_db()
        self.assertIsNone(self.sa.mark)

    def test_marks_outside_zero_to_hundred_are_refused(self):
        self.client.force_login(self.lecturer)
        self.client.post(self.url(), {f"mark_{self.sa.pk}": "150"})
        self.sa.refresh_from_db()
        self.assertIsNone(self.sa.mark)

    def test_registrar_sees_a_read_only_roster(self):
        self.client.force_login(self.registrar)
        resp = self.client.get(self.url())
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["can_save"])


class MarksReportTests(BaseAssessment):
    def course_url(self):
        return reverse("marks_report", args=["course", self.course.pk])

    def test_mark_sheet_shows_marks_positions_and_average(self):
        self.e_a.mark = Decimal("80")
        self.e_a.save()
        self.e_b.mark = Decimal("60")
        self.e_b.save()
        self.client.force_login(self.lecturer)
        resp = self.client.get(self.course_url())
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "80")
        self.assertContains(resp, "of 2")
        self.assertContains(resp, "70.0")

    def test_owner_lecturer_registrar_and_admin_can_open_it(self):
        for user in (self.lecturer, self.registrar, self.admin):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.course_url()).status_code, 200,
                             user.username)

    def test_another_lecturer_cannot_open_it(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.course_url()).status_code, 403)

    def test_subject_mark_sheet_opens_for_the_subject_teacher(self):
        url = reverse("marks_report", args=["subject", self.subject.pk])
        self.client.force_login(self.lecturer)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 403)


class CohortTableTests(BaseAssessment):
    def test_positions_are_ordered_and_counted_per_cohort(self):
        self.e_a.mark = Decimal("90")
        self.e_a.save()
        self.e_b.mark = Decimal("40")
        self.e_b.save()
        self.sa.mark = Decimal("70")
        self.sa.save()
        sb = Enrollment.objects.create(student=self.b, subject=self.subject,
                                       term=self.term)
        sb.mark = Decimal("50")
        sb.save()
        table = Enrollment.cohort_table([self.e_a, self.e_b, self.sa, sb])
        self.assertEqual(table[self.e_a.pk], (1, 2))
        self.assertEqual(table[self.e_b.pk], (2, 2))
        self.assertEqual(table[self.sa.pk], (1, 2))
        self.assertEqual(table[sb.pk], (2, 2))

    def test_unmarked_rows_have_no_position(self):
        self.e_a.mark = Decimal("50")
        self.e_a.save()
        table = Enrollment.cohort_table([self.e_a, self.e_b])
        self.assertIn(self.e_a.pk, table)
        self.assertNotIn(self.e_b.pk, table)


class StudentReportTests(BaseAssessment):
    def test_student_sees_own_report_card(self):
        self.e_a.mark = Decimal("77")
        self.e_a.save()
        self.client.force_login(self.a.user)
        resp = self.client.get(reverse("my_report"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Report card")
        self.assertContains(resp, "77")
        self.assertContains(resp, "1 of 2")

    def test_student_report_does_not_show_other_students(self):
        self.e_b.mark = Decimal("91")
        self.e_b.save()
        self.client.force_login(self.a.user)
        resp = self.client.get(reverse("my_report"))
        self.assertContains(resp, "Alice")
        self.assertNotContains(resp, "Bob")

    def test_lecturer_report_only_shows_their_own_rows(self):
        other_course = Course.objects.create(code="NET101", title="Networking",
                                             programme=self.programme,
                                             lecturer=self.other)
        e_other = Enrollment.objects.create(student=self.a, course=other_course,
                                            term=self.term)
        e_other.mark = Decimal("88")
        e_other.save()
        self.e_a.mark = Decimal("77")
        self.e_a.save()
        self.client.force_login(self.lecturer)
        resp = self.client.get(reverse("student_report", args=[self.a.pk]))
        self.assertContains(resp, "IT101")
        self.assertNotContains(resp, "NET101")
        # Look inside the marks table so the CSRF token cannot trip the check.
        self.assertNotIn(">88<", resp.content.decode())

    def test_registrar_and_bursar_can_open_the_report(self):
        for user in (self.registrar, self.bursar):
            self.client.force_login(user)
            resp = self.client.get(reverse("student_report", args=[self.a.pk]))
            self.assertEqual(resp.status_code, 200, user.username)

    def test_student_cannot_open_someone_elses_report(self):
        self.client.force_login(self.a.user)
        resp = self.client.get(reverse("student_report", args=[self.b.pk]))
        self.assertEqual(resp.status_code, 403)


class GradeScalePageTests(BaseAssessment):
    def test_admin_adds_and_removes_bands(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("grade_bands"),
                                {"letter": "A", "min_mark": "75", "points": "4"})
        self.assertRedirects(resp, reverse("grade_bands"))
        band = GradeBand.objects.get(letter="A")
        resp = self.client.post(reverse("grade_band_delete", args=[band.pk]))
        self.assertRedirects(resp, reverse("grade_bands"))
        self.assertFalse(GradeBand.objects.exists())

    def test_registrar_cannot_open_the_grade_scale(self):
        self.client.force_login(self.registrar)
        self.assertEqual(self.client.get(reverse("grade_bands")).status_code, 403)

    def test_duplicate_letter_shows_a_form_error(self):
        self.client.force_login(self.admin)
        GradeBand.objects.create(letter="A", min_mark=75, points=4)
        resp = self.client.post(reverse("grade_bands"),
                                {"letter": "a", "min_mark": "80", "points": "4"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "already exists")


class PortalLinksTests(BaseAssessment):
    def test_student_portal_links_to_the_report_card(self):
        self.client.force_login(self.a.user)
        resp = self.client.get(reverse("student_portal"))
        self.assertContains(resp, reverse("my_report"))

    def test_student_detail_links_to_the_report_card(self):
        self.client.force_login(self.registrar)
        resp = self.client.get(reverse("student_detail", args=[self.a.pk]))
        self.assertContains(resp, reverse("student_report", args=[self.a.pk]))

    def test_subject_list_links_to_the_roster(self):
        self.client.force_login(self.registrar)
        resp = self.client.get(reverse("subject_list"))
        self.assertContains(resp, reverse("subject_roster", args=[self.subject.pk]))

    def test_lecturer_portal_lists_subjects(self):
        self.client.force_login(self.lecturer)
        resp = self.client.get(reverse("lecturer_portal"))
        self.assertContains(resp, reverse("subject_roster", args=[self.subject.pk]))
