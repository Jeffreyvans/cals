from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from academics.models import (
    AcademicYear,
    Attendance,
    Course,
    Enrollment,
    Programme,
    Student,
    Subject,
    Term,
)
from accounts.models import User


class BaseAttendance(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.lecturer = User.objects.create_user("lec", password="pw123456", role="lecturer")
        cls.other = User.objects.create_user("lec2", password="pw123456", role="lecturer")
        cls.registrar = User.objects.create_user("reg", password="pw123456", role="registrar")
        cls.programme = Programme.objects.create(name="Diploma in IT", code="DIT",
                                                 level="diploma")
        cls.course = Course.objects.create(code="IT101", title="Programming",
                                           programme=cls.programme, lecturer=cls.lecturer)
        cls.subject = Subject.objects.create(code="MTH", name="Mathematics",
                                             level="secondary", lecturer=cls.lecturer)
        ua = User.objects.create_user("alice", password="pw123456", first_name="Alice",
                                      last_name="Ashaw", role="student")
        ub = User.objects.create_user("bob", password="pw123456", first_name="Bob",
                                      last_name="Banda", role="student")
        cls.a = Student.objects.create(user=ua, programme=cls.programme)
        cls.b = Student.objects.create(user=ub, programme=cls.programme)
        cls.year = AcademicYear.objects.create(
            name="2026", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        cls.term = Term.objects.create(
            academic_year=cls.year, name="Term 1", start_date=date(2026, 1, 12),
            end_date=date(2026, 4, 10))
        cls.ea = Enrollment.objects.create(student=cls.a, subject=cls.subject,
                                           term=cls.term)
        cls.eb = Enrollment.objects.create(student=cls.b, subject=cls.subject,
                                           term=cls.term)
        cls.today = date(2026, 3, 3)


class AttendanceEntryTests(BaseAttendance):
    def url(self):
        return reverse("subject_roster", args=[self.subject.pk])

    def test_lecturer_records_attendance_on_the_subject_roster(self):
        self.client.force_login(self.lecturer)
        self.client.post(self.url(), {
            "attendance_date": self.today.isoformat(),
            f"present_{self.ea.pk}": "on"})
        self.assertTrue(Attendance.objects.get(enrollment=self.ea, date=self.today).present)
        self.assertFalse(Attendance.objects.get(enrollment=self.eb, date=self.today).present)

    def test_re_recording_a_date_updates_the_row(self):
        Attendance.objects.create(enrollment=self.ea, date=self.today, present=True)
        self.client.force_login(self.lecturer)
        self.client.post(self.url(), {"attendance_date": self.today.isoformat()})
        self.assertFalse(Attendance.objects.get(enrollment=self.ea, date=self.today).present)
        self.assertEqual(Attendance.objects.filter(enrollment=self.ea).count(), 1)

    def test_bad_date_is_reported_and_nothing_saved(self):
        self.client.force_login(self.lecturer)
        resp = self.client.post(self.url(), {"attendance_date": "nonsense"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Attendance.objects.count(), 0)

    def test_registrar_cannot_record_attendance(self):
        self.client.force_login(self.registrar)
        self.client.post(self.url(), {"attendance_date": self.today.isoformat()})
        self.assertEqual(Attendance.objects.count(), 0)


class RegisterTests(BaseAttendance):
    def course_url(self):
        return reverse("attendance_register", args=["course", self.course.pk])

    def subject_url(self):
        return reverse("attendance_register", args=["subject", self.subject.pk])

    def test_register_shows_cells_and_totals(self):
        cea = Enrollment.objects.create(student=self.a, course=self.course, term=self.term)
        for i, present in enumerate([True, False, True]):
            Attendance.objects.create(enrollment=cea,
                                      date=self.today - timedelta(days=i), present=present)
        self.client.force_login(self.lecturer)
        resp = self.client.get(self.course_url())
        self.assertEqual(resp.status_code, 200)
        row = resp.context["rows"][0]
        self.assertEqual(row.cells, [True, False, True])
        self.assertEqual(row.attended, 2)
        self.assertEqual(resp.context["sessions"], 3)

    def test_owner_registrar_and_admin_can_open_it(self):
        for user in (self.lecturer, self.registrar):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.subject_url()).status_code, 200,
                             user.username)

    def test_another_lecturer_and_students_are_refused(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.subject_url()).status_code, 403)
        self.client.force_login(self.a.user)
        self.assertEqual(self.client.get(self.subject_url()).status_code, 403)


class RatesByTermTests(BaseAttendance):
    def test_counts_are_grouped_by_term(self):
        term2 = Term.objects.create(
            academic_year=self.year, name="Term 2", start_date=date(2026, 5, 4),
            end_date=date(2026, 8, 28))
        eb2 = Enrollment.objects.create(student=self.b, course=self.course, term=term2)
        for present in (True, True, False):
            Attendance.objects.create(enrollment=self.ea, date=self.today, present=present)
            self.today += timedelta(days=1)
        Attendance.objects.create(enrollment=eb2, date=self.today, present=True)
        rates = Attendance.rates_by_term([self.ea, eb2])
        self.assertEqual(rates["Term 1"], (3, 2))
        self.assertEqual(rates["Term 2"], (1, 1))


class ReportAttendanceTests(BaseAttendance):
    def test_report_card_shows_attendance_per_term(self):
        for present in (True, True, False):
            Attendance.objects.create(enrollment=self.ea, date=self.today, present=present)
            self.today += timedelta(days=1)
        self.ea.mark = Decimal("70")
        self.ea.save()
        self.client.force_login(self.a.user)
        resp = self.client.get(reverse("my_report"))
        self.assertContains(resp, "2 of 3 sessions")
        self.assertContains(resp, "(67%)")

    def test_student_portal_shows_attendance_table(self):
        for present in (True, False):
            Attendance.objects.create(enrollment=self.ea, date=self.today, present=present)
            self.today += timedelta(days=1)
        self.client.force_login(self.a.user)
        resp = self.client.get(reverse("student_portal"))
        self.assertContains(resp, "My attendance")
        self.assertContains(resp, "50%")

    def test_rosters_and_mark_sheets_link_to_the_register(self):
        self.client.force_login(self.registrar)
        roster = self.client.get(reverse("subject_roster", args=[self.subject.pk]))
        self.assertContains(roster, reverse("attendance_register",
                                            args=["subject", self.subject.pk]))
        sheet = self.client.get(reverse("marks_report", args=["subject", self.subject.pk]))
        self.assertContains(sheet, reverse("attendance_register",
                                           args=["subject", self.subject.pk]))
