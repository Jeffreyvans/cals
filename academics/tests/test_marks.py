from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from academics.models import (
    AcademicYear,
    Activity,
    AssessmentComponent,
    AssessmentMark,
    Attendance,
    Course,
    Enrollment,
    Programme,
    Student,
    Subject,
    Term,
)

User = get_user_model()


class MarksAndAttendanceTests(TestCase):
    def setUp(self):
        self.lecturer = User.objects.create_user("lec", password="pw123456", role="lecturer")
        self.lecturer2 = User.objects.create_user("lec2", password="pw123456", role="lecturer")
        self.registrar = User.objects.create_user("reg", password="pw123456", role="registrar")
        programme = Programme.objects.create(name="Diploma in IT", code="DIT", level="diploma")
        u1 = User.objects.create_user("s1", password="pw123456", role="student")
        u2 = User.objects.create_user("s2", password="pw123456", role="student")
        self.s1 = Student.objects.create(user=u1, programme=programme, student_no="S0201")
        self.s2 = Student.objects.create(user=u2, programme=programme, student_no="S0202")
        self.course = Course.objects.create(code="IT101", title="Programming",
                                            programme=programme, lecturer=self.lecturer)
        self.other_course = Course.objects.create(code="IT102", title="Networking",
                                                  programme=programme, lecturer=self.lecturer2)
        year = AcademicYear.objects.create(
            name="2026", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        self.term = Term.objects.create(
            academic_year=year, name="Term 1", start_date=date(2026, 1, 12),
            end_date=date(2026, 4, 10))
        self.e1 = Enrollment.objects.create(student=self.s1, course=self.course,
                                            term=self.term)
        self.e2 = Enrollment.objects.create(student=self.s2, course=self.course,
                                            term=self.term)

    def test_lecturer_saves_marks_on_own_course(self):
        self.client.force_login(self.lecturer)
        resp = self.client.post(reverse("course_roster", args=[self.course.pk]), {
            "mark_1": "75.5", "mark_2": "42", "attendance_date": "2026-01-12",
            f"present_{self.e1.pk}": "on"})
        self.assertRedirects(resp, reverse("course_roster", args=[self.course.pk]))
        self.e1.refresh_from_db()
        self.e2.refresh_from_db()
        self.assertEqual(self.e1.mark, Decimal("75.5"))
        self.assertEqual(self.e2.mark, Decimal("42"))
        self.assertTrue(Attendance.objects.filter(enrollment=self.e1, date=date(2026, 1, 12),
                                                  present=True).exists())
        self.assertFalse(Attendance.objects.filter(enrollment=self.e2, date=date(2026, 1, 12),
                                                   present=True).exists())

    def test_marks_outside_zero_to_hundred_are_rejected(self):
        self.client.force_login(self.lecturer)
        self.client.post(reverse("course_roster", args=[self.course.pk]),
                         {"mark_1": "150", "mark_2": "abc", "attendance_date": ""})
        self.e1.refresh_from_db()
        self.e2.refresh_from_db()
        self.assertIsNone(self.e1.mark)
        self.assertIsNone(self.e2.mark)

    def test_lecturer_cannot_change_marks_on_another_course(self):
        Enrollment.objects.create(student=self.s1, course=self.other_course,
                                  term=self.term)
        self.client.force_login(self.lecturer)
        resp = self.client.post(reverse("course_roster", args=[self.other_course.pk]), {"mark_1": "99"})
        self.assertEqual(resp.status_code, 403)

    def test_bad_attendance_date_is_reported_not_crashed(self):
        self.client.force_login(self.lecturer)
        resp = self.client.post(reverse("course_roster", args=[self.course.pk]),
                                 {"attendance_date": "not-a-date"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Attendance.objects.count(), 0)


class AssessmentComponentTests(TestCase):
    def setUp(self):
        self.lecturer = User.objects.create_user("lec", password="pw123456", role="lecturer")
        self.other = User.objects.create_user("lec2", password="pw123456", role="lecturer")
        self.registrar = User.objects.create_user("reg", password="pw123456", role="registrar")
        programme = Programme.objects.create(name="Diploma in IT", code="DIT", level="diploma")
        u1 = User.objects.create_user("s1", password="pw123456", role="student")
        u2 = User.objects.create_user("s2", password="pw123456", role="student")
        self.s1 = Student.objects.create(user=u1, programme=programme, student_no="S0401")
        self.s2 = Student.objects.create(user=u2, programme=programme, student_no="S0402")
        self.course = Course.objects.create(code="IT101", title="Programming",
                                            programme=programme, lecturer=self.lecturer)
        self.subject = Subject.objects.create(code="MTH101", name="Mathematics",
                                              level="college", lecturer=self.lecturer)
        year = AcademicYear.objects.create(
            name="2026", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        self.term = Term.objects.create(
            academic_year=year, name="Term 1", start_date=date(2026, 1, 12),
            end_date=date(2026, 4, 10))
        self.e1 = Enrollment.objects.create(student=self.s1, course=self.course,
                                            term=self.term)
        self.e2 = Enrollment.objects.create(student=self.s2, course=self.course,
                                            term=self.term)
        self.se1 = Enrollment.objects.create(student=self.s1, subject=self.subject,
                                             term=self.term)

    def add_component(self, kind, item, name, weight, order=0):
        self.client.force_login(self.lecturer)
        return self.client.post(
            reverse("assessment_components", args=[kind, item.pk]),
            {"name": name, "weight": weight, "order": order})

    def test_components_produce_a_weighted_final_mark(self):
        self.add_component("course", self.course, "CA", "40", 0)
        self.add_component("course", self.course, "Exam", "60", 1)
        ca = AssessmentComponent.objects.get(course=self.course, name="CA")
        exam = AssessmentComponent.objects.get(course=self.course, name="Exam")
        resp = self.client.post(reverse("course_roster", args=[self.course.pk]), {
            f"component_{ca.pk}_{self.e1.pk}": "80",
            f"component_{exam.pk}_{self.e1.pk}": "60",
            f"component_{ca.pk}_{self.e2.pk}": "70",
            f"component_{exam.pk}_{self.e2.pk}": "90",
        })
        self.assertRedirects(resp, reverse("course_roster", args=[self.course.pk]))
        self.e1.refresh_from_db()
        self.e2.refresh_from_db()
        self.assertEqual(self.e1.mark, Decimal("68.00"))
        self.assertEqual(self.e2.mark, Decimal("82.00"))

    def test_roster_shows_one_input_per_component(self):
        self.add_component("course", self.course, "CA", "40")
        ca = AssessmentComponent.objects.get(course=self.course, name="CA")
        resp = self.client.get(reverse("course_roster", args=[self.course.pk]))
        self.assertContains(resp, f"component_{ca.pk}_{self.e1.pk}")
        self.assertContains(resp, "CA")

    def test_final_mark_stays_blank_until_every_component_is_marked(self):
        self.add_component("course", self.course, "CA", "40")
        self.add_component("course", self.course, "Exam", "60")
        ca = AssessmentComponent.objects.get(course=self.course, name="CA")
        self.client.post(reverse("course_roster", args=[self.course.pk]),
                         {f"component_{ca.pk}_{self.e1.pk}": "80"})
        self.e1.refresh_from_db()
        self.assertIsNone(self.e1.mark)
        self.assertEqual(AssessmentMark.objects.filter(enrollment=self.e1).count(), 1)

    def test_component_mark_outside_zero_to_hundred_is_refused(self):
        self.add_component("course", self.course, "CA", "40")
        self.add_component("course", self.course, "Exam", "60")
        ca = AssessmentComponent.objects.get(course=self.course, name="CA")
        exam = AssessmentComponent.objects.get(course=self.course, name="Exam")
        resp = self.client.post(reverse("course_roster", args=[self.course.pk]), {
            f"component_{ca.pk}_{self.e1.pk}": "150",
            f"component_{exam.pk}_{self.e1.pk}": "50",
        }, follow=True)
        self.assertContains(resp, "between 0 and 100")
        self.e1.refresh_from_db()
        self.assertIsNone(self.e1.mark)
        self.assertFalse(AssessmentMark.objects.filter(mark=Decimal("150")).exists())

    def test_a_component_name_cannot_be_repeated(self):
        self.add_component("course", self.course, "CA", "40")
        resp = self.add_component("course", self.course, "ca", "60")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(AssessmentComponent.objects.filter(course=self.course).count(), 1)

    def test_only_the_teacher_in_charge_can_change_the_setup(self):
        url = reverse("assessment_components", args=["course", self.course.pk])
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(
            url, {"name": "CA", "weight": "40", "order": "0"}).status_code, 403)
        self.client.force_login(self.registrar)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertFalse(AssessmentComponent.objects.filter(course=self.course).exists())

    def test_removing_a_component_recalculates_the_final_mark(self):
        self.add_component("course", self.course, "CA", "40")
        self.add_component("course", self.course, "Exam", "60")
        ca = AssessmentComponent.objects.get(course=self.course, name="CA")
        exam = AssessmentComponent.objects.get(course=self.course, name="Exam")
        self.client.post(reverse("course_roster", args=[self.course.pk]), {
            f"component_{ca.pk}_{self.e1.pk}": "80",
            f"component_{exam.pk}_{self.e1.pk}": "60",
        })
        self.e1.refresh_from_db()
        self.assertEqual(self.e1.mark, Decimal("68.00"))
        self.client.post(reverse("assessment_components", args=["course", self.course.pk]),
                         {"action": "delete", "pk": exam.pk})
        self.e1.refresh_from_db()
        self.assertEqual(self.e1.mark, Decimal("80.00"))

    def test_subject_components_appear_on_the_roster_and_mark_sheet(self):
        self.add_component("subject", self.subject, "CA", "50")
        ca = AssessmentComponent.objects.get(subject=self.subject, name="CA")
        resp = self.client.post(reverse("subject_roster", args=[self.subject.pk]),
                                {f"component_{ca.pk}_{self.se1.pk}": "70"})
        self.assertRedirects(resp, reverse("subject_roster", args=[self.subject.pk]))
        self.se1.refresh_from_db()
        self.assertEqual(self.se1.mark, Decimal("70.00"))
        resp = self.client.get(reverse("marks_report", args=["subject", self.subject.pk]))
        self.assertContains(resp, "CA")
        self.assertContains(resp, "Final")


class StudentPortalScopeTests(TestCase):
    def test_student_only_sees_own_courses(self):
        programme = Programme.objects.create(name="Form 1", code="F1", level="secondary")
        u1 = User.objects.create_user("a1", password="pw123456", role="student")
        u2 = User.objects.create_user("a2", password="pw123456", role="student")
        Student.objects.create(user=u1, programme=programme, student_no="S0301")
        s2 = Student.objects.create(user=u2, programme=programme, student_no="S0302")
        course = Course.objects.create(code="ENG101", title="English", programme=programme)
        year = AcademicYear.objects.create(
            name="2026", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        term = Term.objects.create(
            academic_year=year, name="Term 1", start_date=date(2026, 1, 12),
            end_date=date(2026, 4, 10))
        Enrollment.objects.create(student=s2, course=course, term=term)
        Activity.objects.create(student=s2, kind="sport", title="Athletics day",
                                date=date.today())
        self.client.force_login(u1)
        resp = self.client.get(reverse("student_portal"))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertNotIn("ENG101", content)
        self.assertNotIn("Athletics day", content)
        self.assertNotIn("S0302", content)
