from django.db import IntegrityError, transaction
from django.test import TestCase

from academics.factories import StudentFactory
from student_affairs.models import Club, ClubMembership, EventRegistration


class StudentAffairsTests(TestCase):
    def test_a_student_can_only_join_a_club_once(self):
        student = StudentFactory(status="active")
        club = Club.objects.create(name="Debate")
        ClubMembership.objects.create(club=club, student=student)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ClubMembership.objects.create(club=club, student=student)

    def test_an_event_registration_is_unique(self):
        student = StudentFactory(status="active")
        from student_affairs.models import Event

        event = Event.objects.create(title="Careers day", date="2026-03-01")
        EventRegistration.objects.create(event=event, student=student)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                EventRegistration.objects.create(event=event, student=student)
