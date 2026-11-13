from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from academics.factories import StudentFactory
from careers.models import AttachmentGrade, Employer, Placement
from careers.services import grade_placement
from core.models import Institution


class CareersTests(TestCase):
    def test_a_placement_cannot_end_before_it_starts(self):
        placement = Placement(
            title="Attachment", start_date="2026-03-01", end_date="2026-02-01"
        )
        with self.assertRaises(ValidationError):
            placement.clean()

        placement.end_date = "2026-04-01"
        placement.clean()


class AttachmentGradingTests(TestCase):
    def setUp(self):
        self.institution = Institution.objects.get(slug="default")
        self.student = StudentFactory(status="active")
        self.employer = Employer.objects.create(name="Delta Motors")
        self.placement = Placement.objects.create(
            student=self.student,
            employer=self.employer,
            title="Engineering attachment",
            start_date=date(2026, 1, 5),
            status=Placement.Status.ACTIVE,
        )

    def test_grading_completes_the_placement_and_sets_a_letter(self):
        grade = grade_placement(self.placement, "78.00", "Excellent work.")
        self.assertEqual(grade.mark, Decimal("78.00"))
        self.assertEqual(grade.letter, "A")
        self.placement.refresh_from_db()
        self.assertEqual(self.placement.status, Placement.Status.COMPLETED)
        self.assertEqual(self.student.user.notifications.count(), 1)

    def test_regrading_replaces_the_mark(self):
        grade_placement(self.placement, "62.00")
        grade = grade_placement(self.placement, "55.00")
        self.assertEqual(AttachmentGrade.objects.filter(placement=self.placement).count(), 1)
        self.assertEqual(grade.letter, "C")

    def test_a_mark_outside_the_range_is_rejected(self):
        with self.assertRaises(ValidationError):
            grade_placement(self.placement, "140")
        self.assertFalse(AttachmentGrade.objects.filter(placement=self.placement).exists())
