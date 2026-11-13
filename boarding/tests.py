from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from academics.factories import StudentFactory
from accounts.factories import UserFactory
from boarding.models import Boarder, Exeat, Hostel, Room
from boarding.services import allocate_rooms, allocation_plan
from core.models import Institution
from core.modules import set_module
from core.roles import ensure_roles


class BoardingTests(TestCase):
    def test_an_exeat_must_end_after_it_starts(self):
        start = timezone.now()
        exeat = Exeat(starts_at=start, ends_at=start - timedelta(hours=1))
        with self.assertRaises(ValidationError):
            exeat.clean()

        exeat.ends_at = start + timedelta(hours=2)
        exeat.clean()


class BoardingAllocationTests(TestCase):
    def setUp(self):
        self.institution = Institution.objects.get(slug="default")
        ensure_roles(self.institution)
        set_module(self.institution, "boarding", True)
        self.admin = UserFactory(
            username="boarding_admin", role="admin", institution=self.institution
        )
        self.hostel = Hostel.objects.create(
            institution=self.institution, name="Kariba House", capacity=4
        )
        self.room = Room.objects.create(
            institution=self.institution, hostel=self.hostel, name="A1", capacity=2
        )

    def _boarder(self, name, **extra):
        user = UserFactory(
            username=name, role="student", institution=self.institution
        )
        student = StudentFactory(user=user, status="active")
        return Boarder.objects.create(
            institution=self.institution, student=student, **extra
        )

    def test_unallocated_boarders_are_placed_in_free_rooms(self):
        first = self._boarder("boarder_one")
        second = self._boarder("boarder_two")
        plan = allocation_plan()
        self.assertEqual(len(plan["assignments"]), 2)
        allocate_rooms()
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.room, self.room)
        self.assertEqual(first.bed, "1")
        self.assertEqual(second.bed, "2")

    def test_boarders_beyond_capacity_wait(self):
        self._boarder("boarder_three")
        self._boarder("boarder_four")
        waiting = self._boarder("boarder_five")
        plan = allocation_plan()
        self.assertEqual(len(plan["assignments"]), 2)
        self.assertEqual(plan["unplaced"], [waiting])

    def test_the_allocation_screen_previews_and_applies(self):
        boarder = self._boarder("boarder_screen")
        self.client.force_login(self.admin)
        page = self.client.get(reverse("boarding:allocate"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Kariba House")
        response = self.client.post(reverse("boarding:allocate"))
        self.assertEqual(response.status_code, 302)
        boarder.refresh_from_db()
        self.assertEqual(boarder.room, self.room)
