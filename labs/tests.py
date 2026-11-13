from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from academics.factories import StudentFactory
from labs.models import (
    Consumable,
    ConsumableIssue,
    EquipmentBooking,
    Lab,
    PracticalAssessment,
    SafetyInduction,
)


class LabTests(TestCase):
    def test_a_booking_must_end_after_it_starts(self):
        booking = EquipmentBooking(
            purpose="Practical", starts_at="2026-03-01T10:00", ends_at="2026-03-01T09:00"
        )
        with self.assertRaises(ValidationError):
            booking.clean()

        booking.ends_at = "2026-03-01T11:00"
        booking.clean()


class LabStockAndSafetyTests(TestCase):
    def setUp(self):
        self.lab = Lab.objects.create(name="Chemistry")
        self.student = StudentFactory(status="active")

    def test_an_induction_expires(self):
        induction = SafetyInduction.objects.create(
            lab=self.lab,
            student=self.student,
            expires_on=timezone.localdate() - timedelta(days=1),
        )
        self.assertFalse(induction.is_valid)
        induction.expires_on = timezone.localdate() + timedelta(days=30)
        self.assertTrue(induction.is_valid)

    def test_issuing_consumables_reduces_the_stock(self):
        item = Consumable.objects.create(
            lab=self.lab, name="Gloves", quantity=Decimal("50")
        )
        ConsumableIssue.objects.create(
            consumable=item, student=self.student, quantity=Decimal("10")
        )
        item.refresh_from_db()
        self.assertEqual(item.quantity, Decimal("40.00"))

    def test_a_practical_mark_cannot_exceed_the_maximum(self):
        assessment = PracticalAssessment(
            student=self.student, title="Titration", mark=Decimal("80"), max_mark=Decimal("50")
        )
        with self.assertRaises(ValidationError):
            assessment.clean()
        assessment.mark = Decimal("45")
        assessment.clean()
        self.assertEqual(assessment.percentage, Decimal("90.0"))
