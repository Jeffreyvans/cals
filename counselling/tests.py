from django.test import TestCase
from django.urls import reverse

from academics.factories import StudentFactory
from accounts.factories import UserFactory
from core.models import Institution, Role
from core.modules import set_module
from core.roles import ensure_roles
from counselling.models import CounsellingSession, Referral


class CounsellingTests(TestCase):
    def test_a_session_belongs_to_the_student_and_the_counsellor(self):
        student = StudentFactory(status="active")
        session = CounsellingSession.objects.create(student=student, topic="Exam stress")
        self.assertIn(str(student), str(session))
        self.assertTrue(session.confidential)

    def test_a_referral_starts_open(self):
        student = StudentFactory(status="active")
        referral = Referral.objects.create(
            student=student, referred_to="Clinic", reason="Needs a check-up"
        )
        self.assertEqual(referral.status, Referral.Status.OPEN)
        self.assertEqual(str(referral), f"{student} to Clinic")


class CounsellingAuthorScopeTests(TestCase):
    def setUp(self):
        self.institution = Institution.objects.get(slug="default")
        ensure_roles(self.institution)
        set_module(self.institution, "counselling", True)
        self.counsellor = self._counsellor("counsellor_one")
        self.other = self._counsellor("counsellor_two")
        self.admin = UserFactory(
            username="counselling_admin", role="admin", institution=self.institution
        )
        self.student = StudentFactory(status="active")
        self.own_session = CounsellingSession.objects.create(
            institution=self.institution,
            student=self.student,
            counsellor=self.counsellor,
            topic="Own private note",
        )
        self.other_session = CounsellingSession.objects.create(
            institution=self.institution,
            student=self.student,
            counsellor=self.other,
            topic="Somebody else's private note",
        )

    def _counsellor(self, username):
        user = UserFactory(
            username=username, role="student", institution=self.institution
        )
        user.primary_role = Role.all_objects.get(
            institution=self.institution, code="counsellor"
        )
        user.save()
        return user

    def test_a_counsellor_only_lists_their_own_sessions(self):
        self.client.force_login(self.counsellor)
        response = self.client.get(reverse("counselling:list", args=["sessions"]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Own private note")
        self.assertNotContains(response, "Somebody else's private note")

    def test_a_counsellor_cannot_open_another_counsellors_session(self):
        self.client.force_login(self.counsellor)
        response = self.client.get(
            reverse("counselling:detail", args=["sessions", self.other_session.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_the_json_register_also_hides_other_counsellors_notes(self):
        self.client.force_login(self.counsellor)
        response = self.client.get(reverse("counselling:api_records", args=["sessions"]))
        self.assertEqual(response.status_code, 200)
        labels = [row["label"] for row in response.json()["results"]]
        self.assertTrue(any("Own private note" in label for label in labels))
        self.assertFalse(any("Somebody else" in label for label in labels))

    def test_an_admin_with_view_all_notes_sees_every_counsellor(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("counselling:list", args=["sessions"]))
        self.assertContains(response, "Own private note")
        self.assertContains(response, "Somebody else")
