from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from academics.models import CalendarEvent, Guardian, Programme, SchoolClass, Student
from accounts.models import User


class BaseCalendar(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.registrar = User.objects.create_user(
            "regcal", password="pw123456", role="registrar")
        cls.lecturer = User.objects.create_user(
            "leccal", password="pw123456", role="lecturer")
        cls.programme = Programme.objects.create(
            name="College", code="CLG", level="college")
        cls.g1 = SchoolClass.objects.create(name="Form 1", level="secondary")
        cls.g2 = SchoolClass.objects.create(name="Form 2", level="secondary")
        cls.student = Student.objects.create(
            user=User.objects.create_user("kidcal", password="pw123456", role="student"),
            programme=cls.programme, school_class=cls.g1)
        cls.parent = User.objects.create_user(
            "mumcal", password="pw123456", role="parent")
        Guardian.objects.create(parent=cls.parent, student=cls.student)
        cls.today = timezone.localdate()

    def event(self, title, **kw):
        kw.setdefault("starts_on", self.today + timedelta(days=2))
        return CalendarEvent.objects.create(title=title, **kw)

    def titles(self, user):
        return [e.title for e in CalendarEvent.upcoming(user)]


class CalendarScreenTests(BaseCalendar):
    def test_registrar_adds_an_event(self):
        self.client.force_login(self.registrar)
        resp = self.client.post(reverse("academic_years"), {
            "action": "new_event", "title": "Mid-term break", "kind": "holiday",
            "starts_on": (self.today + timedelta(days=5)).isoformat(),
            "ends_on": (self.today + timedelta(days=9)).isoformat(),
            "detail": "No lessons"})
        self.assertRedirects(resp, reverse("academic_years"))
        event = CalendarEvent.objects.get(title="Mid-term break")
        self.assertEqual(event.kind, CalendarEvent.Kind.HOLIDAY)
        self.assertIn(" – ", event.when)
        page = self.client.get(reverse("academic_years"))
        self.assertContains(page, "Mid-term break")
        self.assertContains(page, "Everyone")

    def test_an_event_cannot_end_before_it_starts(self):
        self.client.force_login(self.registrar)
        resp = self.client.post(reverse("academic_years"), {
            "action": "new_event", "title": "Backwards", "kind": "event",
            "starts_on": (self.today + timedelta(days=5)).isoformat(),
            "ends_on": (self.today + timedelta(days=2)).isoformat()})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "must end after it starts")
        self.assertFalse(CalendarEvent.objects.filter(title="Backwards").exists())

    def test_registrar_removes_an_event(self):
        event = self.event("Old assembly")
        self.client.force_login(self.registrar)
        resp = self.client.post(reverse("academic_years"), {
            "action": "delete_event", "pk": event.pk})
        self.assertRedirects(resp, reverse("academic_years"))
        self.assertFalse(CalendarEvent.objects.filter(pk=event.pk).exists())

    def test_a_lecturer_cannot_change_the_calendar(self):
        self.client.force_login(self.lecturer)
        self.assertEqual(self.client.get(reverse("academic_years")).status_code, 403)
        self.client.post(reverse("academic_years"), {
            "action": "new_event", "title": "Sneaky", "kind": "event",
            "starts_on": self.today.isoformat()})
        self.assertFalse(CalendarEvent.objects.filter(title="Sneaky").exists())


class UpcomingEventsTests(BaseCalendar):
    def test_upcoming_skips_finished_events_and_keeps_running_ones(self):
        self.event("Finished", starts_on=self.today - timedelta(days=5),
                   ends_on=self.today - timedelta(days=2))
        self.event("Running", starts_on=self.today - timedelta(days=1),
                   ends_on=self.today + timedelta(days=1))
        self.event("Later", starts_on=self.today + timedelta(days=3))
        self.event("Today only", starts_on=self.today)
        self.assertEqual(self.titles(self.registrar),
                         ["Running", "Today only", "Later"])

    def test_students_see_the_whole_institution_and_their_class(self):
        self.event("Sports day")
        self.event("Form 1 trip", school_class=self.g1)
        self.event("Form 2 trip", school_class=self.g2)
        self.assertEqual(self.titles(self.student.user),
                         ["Sports day", "Form 1 trip"])

    def test_parents_see_their_childrens_class_events(self):
        self.event("Sports day")
        self.event("Form 1 trip", school_class=self.g1)
        self.event("Form 2 trip", school_class=self.g2)
        self.assertEqual(self.titles(self.parent),
                         ["Sports day", "Form 1 trip"])

    def test_the_limit_keeps_the_soonest_events(self):
        for offset in range(1, 5):
            self.event(f"Event {offset}", starts_on=self.today + timedelta(days=offset))
        self.assertEqual(len(CalendarEvent.upcoming(self.registrar, limit=2)), 2)
        self.assertEqual(self.titles(self.registrar)[:2], ["Event 1", "Event 2"])

    def test_the_student_portal_shows_upcoming_events(self):
        self.event("Sports day")
        self.client.force_login(self.student.user)
        resp = self.client.get(reverse("student_portal"))
        self.assertContains(resp, "Sports day")
        self.assertContains(resp, "Coming up")

    def test_the_staff_dashboard_shows_upcoming_events(self):
        self.event("Sports day")
        self.client.force_login(self.registrar)
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Sports day")
