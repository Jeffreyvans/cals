from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from academics.factories import StudentFactory
from research.models import Milestone, ResearchProject, Thesis


class ResearchTests(TestCase):
    def test_a_project_cannot_end_before_it_starts(self):
        project = ResearchProject(
            title="Water quality", start_date="2026-03-01", end_date="2026-02-01"
        )
        with self.assertRaises(ValidationError):
            project.clean()

        project.end_date = "2026-06-01"
        project.clean()


class ResearchMilestoneTests(TestCase):
    def setUp(self):
        self.project = ResearchProject.objects.create(
            title="Water quality", student=StudentFactory(status="active")
        )

    def test_a_milestone_past_its_due_date_is_overdue_until_completed(self):
        milestone = Milestone.objects.create(
            project=self.project,
            title="Field work",
            due_date=timezone.localdate() - timedelta(days=2),
        )
        self.assertTrue(milestone.is_overdue)
        milestone.status = Milestone.Status.COMPLETED
        self.assertFalse(milestone.is_overdue)

    def test_a_project_has_one_thesis(self):
        thesis = Thesis.objects.create(
            project=self.project,
            title="Water quality in Harare",
            status=Thesis.Status.SUBMITTED,
        )
        self.assertEqual(thesis.project, self.project)
        self.assertEqual(thesis.get_status_display(), "Submitted")
        self.assertEqual(str(thesis), "Water quality in Harare (Submitted)")
