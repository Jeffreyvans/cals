from django.test import TestCase
from django.urls import reverse

from academics.factories import StudentFactory
from accounts.factories import UserFactory
from core.models import Institution, Role, SensitiveViewLog
from core.modules import set_module
from core.roles import ensure_roles
from health.models import ClinicVisit, HealthProfile


class HealthTests(TestCase):
    def test_a_profile_reports_whether_it_has_an_allergy(self):
        student = StudentFactory(status="active")
        profile = HealthProfile.objects.create(student=student, allergies="")
        self.assertFalse(profile.has_allergy)
        profile.allergies = "Peanuts"
        self.assertTrue(profile.has_allergy)

    def test_a_clinic_visit_records_the_outcome(self):
        student = StudentFactory(status="active")
        visit = ClinicVisit.objects.create(
            student=student, complaint="Headache", outcome=ClinicVisit.Outcome.SENT_HOME
        )
        self.assertEqual(visit.get_outcome_display(), "Sent home")


class TeacherSafeAlertTests(TestCase):
    def setUp(self):
        self.institution = Institution.objects.get(slug="default")
        ensure_roles(self.institution)
        set_module(self.institution, "health", True)
        self.teacher = UserFactory(
            username="health_teacher", role="lecturer", institution=self.institution
        )
        self.teacher.primary_role = Role.all_objects.get(
            institution=self.institution, code="lecturer"
        )
        self.teacher.save()
        self.shared = StudentFactory(status="active")
        self.private = StudentFactory(status="active")
        HealthProfile.objects.create(
            institution=self.institution,
            student=self.shared,
            allergies="Dust",
            notes="Full private history",
            share_with_teachers=True,
            teacher_note="Carries an inhaler during sport",
        )
        HealthProfile.objects.create(
            institution=self.institution,
            student=self.private,
            allergies="Penicillin",
            notes="Private counselling referral",
            share_with_teachers=False,
            teacher_note="Not for teachers",
        )

    def test_a_teacher_only_sees_the_notes_cleared_for_teachers(self):
        self.client.force_login(self.teacher)
        response = self.client.get(reverse("health:safe_alerts"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Carries an inhaler during sport")
        self.assertNotContains(response, "Penicillin")
        self.assertNotContains(response, "Private counselling referral")
        self.assertNotContains(response, "Full private history")
        self.assertGreaterEqual(
            SensitiveViewLog.objects.filter(
                record_type="HealthProfile", record_id="safe_alerts"
            ).count(),
            1,
        )

    def test_the_safe_alert_screen_is_listed_for_teachers(self):
        self.client.force_login(self.teacher)
        response = self.client.get(reverse("module_directory"))
        self.assertContains(response, "Teacher-safe health alerts")

    def test_a_student_cannot_open_the_safe_alert_screen(self):
        student_user = UserFactory(
            username="health_student", role="student", institution=self.institution
        )
        self.client.force_login(student_user)
        self.assertEqual(
            self.client.get(reverse("health:safe_alerts")).status_code, 403
        )
