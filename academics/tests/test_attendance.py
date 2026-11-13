from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from academics.models import (
    AcademicYear,
    Attendance,
    Course,
    Enrollment,
    Guardian,
    Programme,
    Student,
    Subject,
    Term,
)
from accounts.models import User
from core.models import Notification, OutboundMessage


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

    def mark(self, enrollment, status, day=None, **extra):
        return Attendance.objects.create(
            enrollment=enrollment, date=day or self.today, status=status, **extra)


class AttendanceEntryTests(BaseAttendance):
    def url(self):
        return reverse("subject_roster", args=[self.subject.pk])

    def test_lecturer_records_attendance_on_the_subject_roster(self):
        self.client.force_login(self.lecturer)
        self.client.post(self.url(), {
            "attendance_date": self.today.isoformat(),
            f"status_{self.ea.pk}": "present"})
        present = Attendance.objects.get(enrollment=self.ea, date=self.today)
        self.assertEqual(present.status, Attendance.Status.PRESENT)
        self.assertTrue(present.present)
        absent = Attendance.objects.get(enrollment=self.eb, date=self.today)
        self.assertEqual(absent.status, Attendance.Status.ABSENT)
        self.assertFalse(absent.present)

    def test_re_recording_a_date_updates_the_row(self):
        self.mark(self.ea, Attendance.Status.PRESENT)
        self.client.force_login(self.lecturer)
        self.client.post(self.url(), {
            "attendance_date": self.today.isoformat(),
            f"status_{self.ea.pk}": "late"})
        row = Attendance.objects.get(enrollment=self.ea, date=self.today)
        self.assertEqual(row.status, Attendance.Status.LATE)
        self.assertTrue(row.present)
        self.assertEqual(Attendance.objects.filter(enrollment=self.ea).count(), 1)

    def test_marking_a_present_row_absent_flips_the_stored_flag(self):
        # update_or_create only saves the keys it is given, so the presence
        # flag must be kept in step when only the status changes.
        self.mark(self.ea, Attendance.Status.PRESENT)
        self.client.force_login(self.lecturer)
        self.client.post(self.url(), {
            "attendance_date": self.today.isoformat(),
            f"status_{self.ea.pk}": "absent"})
        row = Attendance.objects.get(enrollment=self.ea, date=self.today)
        self.assertEqual(row.status, Attendance.Status.ABSENT)
        self.assertFalse(row.present)

    def test_reason_is_saved_and_shown_on_the_register(self):
        self.client.force_login(self.lecturer)
        self.client.post(self.url(), {
            "attendance_date": self.today.isoformat(),
            f"status_{self.ea.pk}": "excused",
            f"reason_{self.ea.pk}": "Medical appointment"})
        row = Attendance.objects.get(enrollment=self.ea, date=self.today)
        self.assertEqual(row.reason, "Medical appointment")
        self.assertEqual(row.letter, "E")

    def test_an_unknown_status_is_rejected(self):
        self.client.force_login(self.lecturer)
        self.client.post(self.url(), {
            "attendance_date": self.today.isoformat(),
            f"status_{self.ea.pk}": "sleeping"})
        self.assertFalse(
            Attendance.objects.filter(enrollment=self.ea, date=self.today).exists())

    def test_a_new_absence_notifies_each_guardian_once(self):
        parent = User.objects.create_user(
            "mum", password="pw123456", role="parent",
            email="mum@example.com", phone="0777000000")
        other = User.objects.create_user(
            "dad", password="pw123456", role="parent", email="dad@example.com")
        Guardian.objects.create(parent=parent, student=self.a)
        Guardian.objects.create(parent=other, student=self.a)
        self.client.force_login(self.lecturer)

        self.client.post(self.url(), {
            "attendance_date": self.today.isoformat(),
            f"status_{self.ea.pk}": "absent",
            f"reason_{self.ea.pk}": "No note"})
        self.assertEqual(
            Notification.objects.filter(category="attendance").count(), 2)
        # The parent with a phone gets email + SMS; the other only email.
        self.assertEqual(OutboundMessage.objects.count(), 3)
        self.assertTrue(
            Attendance.objects.get(enrollment=self.ea, date=self.today).guardians_notified)

        # Saving the same date again must not tell the parents twice.
        self.client.post(self.url(), {
            "attendance_date": self.today.isoformat(),
            f"status_{self.ea.pk}": "absent"})
        self.assertEqual(
            Notification.objects.filter(category="attendance").count(), 2)
        self.assertEqual(OutboundMessage.objects.count(), 3)

    def test_present_and_late_do_not_notify_parents(self):
        parent = User.objects.create_user(
            "quiet", password="pw123456", role="parent", email="quiet@example.com")
        Guardian.objects.create(parent=parent, student=self.a)
        self.client.force_login(self.lecturer)
        for status in ("present", "late"):
            self.client.post(self.url(), {
                "attendance_date": self.today.isoformat(),
                f"status_{self.ea.pk}": status})
        self.assertFalse(Notification.objects.filter(category="attendance").exists())

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
        for i, status in enumerate(("present", "absent", "present")):
            Attendance.objects.create(enrollment=cea,
                                      date=self.today - timedelta(days=i),
                                      status=status)
        self.client.force_login(self.lecturer)
        resp = self.client.get(self.course_url())
        self.assertEqual(resp.status_code, 200)
        row = resp.context["rows"][0]
        self.assertEqual([letter for letter, _reason in row.cells], ["P", "A", "P"])
        self.assertEqual(row.attended, 2)
        self.assertEqual(row.missed, 1)
        self.assertEqual(resp.context["sessions"], 3)
        self.assertEqual(resp.context["per_date"][0]["attended"], 1)

    def test_the_register_counts_late_as_attended_and_excused_as_missed(self):
        for i, status in enumerate(("late", "excused")):
            Attendance.objects.create(enrollment=self.ea,
                                      date=self.today - timedelta(days=i),
                                      status=status)
        self.client.force_login(self.lecturer)
        resp = self.client.get(self.subject_url())
        row = resp.context["rows"][0] if resp.context["rows"][0].pk == self.ea.pk \
            else resp.context["rows"][1]
        self.assertEqual([letter for letter, _reason in row.cells], ["E", "L"])
        self.assertEqual(row.attended, 1)
        self.assertEqual(row.missed, 1)

    def test_the_register_shows_the_absence_reason_as_a_tooltip(self):
        Attendance.objects.create(enrollment=self.ea, date=self.today,
                                  status="absent", reason="Family emergency")
        self.client.force_login(self.lecturer)
        resp = self.client.get(self.subject_url())
        self.assertContains(resp, 'title="Family emergency"')

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
        day = self.today
        for status in ("present", "late", "absent"):
            Attendance.objects.create(enrollment=self.ea, date=day, status=status)
            day += timedelta(days=1)
        Attendance.objects.create(enrollment=eb2, date=day, status="present")
        rates = Attendance.rates_by_term([self.ea, eb2])
        self.assertEqual(rates["Term 1"], (3, 2))
        self.assertEqual(rates["Term 2"], (1, 1))

    def test_missed_rows_are_listed_newest_first(self):
        day = self.today
        for status in ("absent", "present", "excused"):
            Attendance.objects.create(enrollment=self.ea, date=day, status=status)
            day += timedelta(days=1)
        missed = Attendance.absences([self.ea])
        self.assertEqual([row.status for row in missed], ["excused", "absent"])

    def test_excused_absence_does_not_notify_parents(self):
        parent = User.objects.create_user(
            "ex", password="pw123456", role="parent", email="ex@example.com")
        Guardian.objects.create(parent=parent, student=self.a)
        self.client.force_login(self.lecturer)
        self.client.post(reverse("subject_roster", args=[self.subject.pk]), {
            "attendance_date": self.today.isoformat(),
            f"status_{self.ea.pk}": "excused"})
        self.assertFalse(Notification.objects.filter(category="attendance").exists())


class ReportAttendanceTests(BaseAttendance):
    def test_report_card_shows_attendance_per_term(self):
        day = self.today
        for status in ("present", "present", "absent"):
            Attendance.objects.create(enrollment=self.ea, date=day, status=status)
            day += timedelta(days=1)
        self.ea.mark = Decimal("70")
        self.ea.save()
        self.client.force_login(self.a.user)
        resp = self.client.get(reverse("my_report"))
        self.assertContains(resp, "2 of 3 sessions")
        self.assertContains(resp, "(67%)")

    def test_student_portal_shows_attendance_table(self):
        day = self.today
        for status in ("present", "absent"):
            Attendance.objects.create(enrollment=self.ea, date=day, status=status)
            day += timedelta(days=1)
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
