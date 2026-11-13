from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from sports.models import Equipment, EquipmentLoan, Fixture, HousePoint, Team


class SportsTests(TestCase):
    def test_a_fixture_keeps_its_score_and_result(self):
        team = Team.objects.create(name="First XI", sport="Football")
        fixture = Fixture.objects.create(
            team=team,
            opponent="Rivals",
            date=date(2026, 3, 1),
            score_for=2,
            score_against=1,
            result=Fixture.Result.WIN,
        )
        self.assertEqual(fixture.get_result_display(), "Win")
        self.assertIn("First XI", str(team))

    def test_house_points_can_be_awarded_to_a_house(self):
        point = HousePoint.objects.create(house="Kudu", points=10, reason="Clean-up")
        self.assertEqual(str(point), "Kudu: 10 points")


class EquipmentLoanTests(TestCase):
    def setUp(self):
        from academics.factories import StudentFactory

        self.student = StudentFactory(status="active")
        self.equipment = Equipment.objects.create(
            name="Cricket bat", sport="Cricket", total=2, available=2
        )

    def _loan(self, **extra):
        fields = {
            "equipment": self.equipment,
            "student": self.student,
            "borrowed_on": timezone.localdate(),
            "due_on": timezone.localdate() + timedelta(days=3),
        }
        fields.update(extra)
        return EquipmentLoan.objects.create(**fields)

    def test_issuing_and_returning_equipment_tracks_availability(self):
        loan = self._loan()
        self.equipment.refresh_from_db()
        self.assertEqual(self.equipment.available, 1)
        loan.returned_on = timezone.localdate()
        loan.status = EquipmentLoan.Status.RETURNED
        loan.save(update_fields=["returned_on", "status"])
        self.equipment.refresh_from_db()
        self.assertEqual(self.equipment.available, 2)

    def test_a_loan_entered_as_returned_does_not_take_stock(self):
        self._loan(
            returned_on=timezone.localdate(), status=EquipmentLoan.Status.RETURNED
        )
        self.equipment.refresh_from_db()
        self.assertEqual(self.equipment.available, 2)

    def test_unavailable_equipment_cannot_be_loaned(self):
        self.equipment.available = 0
        self.equipment.save(update_fields=["available"])
        loan = EquipmentLoan(
            equipment=self.equipment,
            student=self.student,
            borrowed_on=timezone.localdate(),
            due_on=timezone.localdate() + timedelta(days=3),
        )
        with self.assertRaises(ValidationError):
            loan.full_clean()

    def test_an_overdue_loan_is_detected(self):
        loan = self._loan(due_on=timezone.localdate() - timedelta(days=1))
        self.assertTrue(loan.is_overdue)
