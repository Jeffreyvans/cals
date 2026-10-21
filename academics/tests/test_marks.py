from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from academics.models import Activity, Attendance, Course, Enrollment, Programme, Student

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
        self.e1 = Enrollment.objects.create(student=self.s1, course=self.course, term="Term 1")
        self.e2 = Enrollment.objects.create(student=self.s2, course=self.course, term="Term 1")

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
        Enrollment.objects.create(student=self.s1, course=self.other_course, term="Term 1")
        self.client.force_login(self.lecturer)
        resp = self.client.post(reverse("course_roster", args=[self.other_course.pk]), {"mark_1": "99"})
        self.assertEqual(resp.status_code, 403)

    def test_bad_attendance_date_is_reported_not_crashed(self):
        self.client.force_login(self.lecturer)
        resp = self.client.post(reverse("course_roster", args=[self.course.pk]),
                                 {"attendance_date": "not-a-date"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Attendance.objects.count(), 0)


class StudentPortalScopeTests(TestCase):
    def test_student_only_sees_own_courses(self):
        programme = Programme.objects.create(name="Form 1", code="F1", level="secondary")
        u1 = User.objects.create_user("a1", password="pw123456", role="student")
        u2 = User.objects.create_user("a2", password="pw123456", role="student")
        s1 = Student.objects.create(user=u1, programme=programme, student_no="S0301")
        s2 = Student.objects.create(user=u2, programme=programme, student_no="S0302")
        course = Course.objects.create(code="ENG101", title="English", programme=programme)
        Enrollment.objects.create(student=s2, course=course, term="Term 1")
        Activity.objects.create(student=s2, kind="sport", title="Athletics day",
                                date=date.today())
        self.client.force_login(u1)
        resp = self.client.get(reverse("student_portal"))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertNotIn("ENG101", content)
        self.assertNotIn("Athletics day", content)
        self.assertNotIn("S0302", content)
