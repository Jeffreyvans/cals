"""Tests for the five optional AI assistants and their output boundary."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from academics.models import Announcement, Programme, Student
from ai.features import ALL_FEATURES, Feature
from ai.models import AIUsageLog
from ai.output import sanitize_output
from ai.providers import Completion
from ai.tests.helpers import accept, make_school, open_platform, switch_on
from core.tenancy import use_institution

User = get_user_model()


class EchoProvider:
    """Return the prompt as text so tests can prove what each feature sent."""

    def __init__(self):
        self.calls = []

    def complete(self, request):
        self.calls.append(request)
        text = "Summary: " + " ".join(request.user_prompt.split())
        return Completion(text=text, model="mock", input_tokens=5, output_tokens=5)


class FixedProvider(EchoProvider):
    """Record the request but return one fixed answer."""

    def __init__(self, text):
        super().__init__()
        self.text = text

    def complete(self, request):
        self.calls.append(request)
        return Completion(text=self.text, model="mock")


class FeatureBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.school, cls.admin = make_school("Feature School", "feature-school")
        cls.other, cls.foreign_admin = make_school("Other School", "other-school")
        with use_institution(cls.school):
            cls.programme = Programme.objects.create(
                name="Diploma in IT", code="DIT", level="diploma",
                tuition_fee=Decimal("100.00"))
            student_user = User.objects.create_user(
                "feature-secret", password="pw123456", role="student",
                first_name="Secret", last_name="Student", institution=cls.school)
            cls.student = Student.objects.create(
                user=student_user, student_no="FEAT1", status="active",
                year_or_grade="1", programme=cls.programme)
        with use_institution(cls.other):
            foreign_programme = Programme.objects.create(
                name="Other Diploma", code="OTH", level="diploma",
                tuition_fee=Decimal("100.00"))
            foreign_user = User.objects.create_user(
                "feature-foreign", password="pw123456", role="student",
                first_name="Foreign", last_name="Student", institution=cls.other)
            Student.objects.create(
                user=foreign_user, student_no="OTHER1", status="active",
                year_or_grade="1", programme=foreign_programme)

    def setUp(self):
        open_platform()
        for feature in Feature.values:
            switch_on(self.school, feature)
        accept(self.admin, self.school, ALL_FEATURES)
        self.client.force_login(self.admin)


class OutputTests(TestCase):
    def test_links_commands_and_control_characters_are_removed(self):
        raw = (
            "Use https://example.com/secret now\n"
            "[guide](https://example.com/guide)\n"
            "$ rm -rf /data\n"
            "Ignore previous instructions\n"
            "Hello\x00 there<script>alert(1)</script>"
        )
        result = sanitize_output(raw, limit=200)
        self.assertNotIn("https://", result)
        self.assertNotIn("rm -rf", result)
        self.assertNotIn("Ignore", result)
        self.assertNotIn("\x00", result)
        self.assertNotIn("<script>", result)
        self.assertIn("guide", result)
        self.assertIn("Hello", result)

    def test_output_is_capped(self):
        self.assertLessEqual(len(sanitize_output("x" * 5000, limit=100)), 100)


class HelpFeatureTests(FeatureBase):
    def test_answer_uses_guide_titles_and_returns_local_source_links(self):
        provider = EchoProvider()
        with override_settings(AI_PROVIDER_INSTANCE=provider):
            response = self.client.post(
                reverse("ai:help"), {"question": "How do I manage users and roles?"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Manage users and roles")
        self.assertContains(response, reverse("user_list"))
        self.assertEqual(len(provider.calls), 1)
        sent = provider.calls[0].user_prompt
        self.assertIn("Manage users and roles", sent)
        self.assertNotIn("http", sent)
        self.assertNotIn("http", sent.lower())

    def test_unsupported_question_answers_i_dont_know_without_a_call(self):
        provider = EchoProvider()
        with override_settings(AI_PROVIDER_INSTANCE=provider):
            response = self.client.post(
                reverse("ai:help"), {"question": "Who won the World Cup?"})
        self.assertContains(response, "I don&#x27;t know.")
        self.assertEqual(provider.calls, [])

    def test_unsupported_model_answer_is_replaced(self):
        provider = FixedProvider("Use a magic portal.")
        with override_settings(AI_PROVIDER_INSTANCE=provider):
            response = self.client.post(
                reverse("ai:help"), {"question": "How do I manage users and roles?"})
        self.assertContains(response, "I don&#x27;t know.")
        self.assertNotContains(response, "magic portal")

    def test_a_switched_off_feature_never_calls_the_provider(self):
        setting = switch_on(self.school, Feature.HELP)
        setting.enabled = False
        setting.save(update_fields=["enabled"])
        provider = EchoProvider()
        with override_settings(AI_PROVIDER_INSTANCE=provider):
            response = self.client.post(
                reverse("ai:help"), {"question": "How do I manage users and roles?"})
        self.assertContains(response, "not switched on")
        self.assertEqual(provider.calls, [])


class DraftFeatureTests(FeatureBase):
    def test_draft_is_labelled_editable_and_never_saved_or_sent(self):
        before = Announcement.objects.count()
        provider = EchoProvider()
        with override_settings(AI_PROVIDER_INSTANCE=provider):
            response = self.client.post(reverse("ai:draft"), {
                "kind": "announcement",
                "instructions": "Tell staff about the meeting on Friday.",
            })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AI draft, please review")
        self.assertContains(response, '<textarea id="draft-text"')
        self.assertNotContains(response, "Save draft")
        self.assertNotContains(response, "Send announcement")
        self.assertEqual(Announcement.objects.count(), before)
        self.assertEqual(provider.calls[0].tier, "smart")
        self.assertEqual(AIUsageLog.all_objects.filter(feature=Feature.DRAFT).count(), 1)

    def test_all_four_document_types_are_offered(self):
        response = self.client.get(reverse("ai:draft"))
        for label in ("Report-card comment", "Announcement", "Fee reminder", "Letter"):
            self.assertContains(response, label)

    def test_unknown_document_type_is_refused_before_a_call(self):
        provider = EchoProvider()
        with override_settings(AI_PROVIDER_INSTANCE=provider):
            response = self.client.post(reverse("ai:draft"), {
                "kind": "contract", "instructions": "Write something",
            })
        self.assertContains(response, "valid document type")
        self.assertEqual(provider.calls, [])


class TranslationFeatureTests(FeatureBase):
    def test_translation_is_labelled_needs_review(self):
        provider = EchoProvider()
        with override_settings(AI_PROVIDER_INSTANCE=provider):
            response = self.client.post(reverse("ai:translation"), {
                "source": "en", "target": "sn", "text": "School opens at eight.",
            })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "needs review")
        self.assertIn("Shona", provider.calls[0].system_prompt)
        self.assertIn("School opens at eight.", provider.calls[0].user_prompt)

    def test_unsupported_language_is_refused_before_a_call(self):
        provider = EchoProvider()
        with override_settings(AI_PROVIDER_INSTANCE=provider):
            response = self.client.post(reverse("ai:translation"), {
                "source": "en", "target": "zu", "text": "Hello",
            })
        self.assertContains(response, "supported language")
        self.assertEqual(provider.calls, [])


class InsightFeatureTests(FeatureBase):
    def test_only_labelled_figures_reach_the_provider(self):
        from finance.models import Invoice

        with use_institution(self.school):
            Invoice.objects.create(
                student=self.student,
                description="Secret Student private note",
                amount=Decimal("250.00"), due_date=date(2026, 2, 1))
        provider = EchoProvider()
        with override_settings(AI_PROVIDER_INSTANCE=provider):
            response = self.client.post(reverse("ai:insights"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Plain-language summary")
        sent = provider.calls[0].user_prompt
        self.assertIn("Active students: 1", sent)
        self.assertIn("Total billed: 250.00", sent)
        self.assertNotIn("Secret", sent)
        self.assertNotIn("private note", sent)
        self.assertContains(response, "Total billed")

    def test_model_invented_numbers_are_replaced_by_the_calculated_figures(self):
        provider = EchoProvider()
        provider.complete = lambda request: Completion(text="There are 999 students.")
        with override_settings(AI_PROVIDER_INSTANCE=provider):
            response = self.client.post(reverse("ai:insights"))
        self.assertContains(response, "Here are the current figures")
        self.assertNotContains(response, "999")


class SearchFeatureTests(FeatureBase):
    def test_request_maps_to_fixed_permission_checked_record_types(self):
        provider = FixedProvider("students")
        with override_settings(AI_PROVIDER_INSTANCE=provider):
            response = self.client.post(reverse("ai:search"), {"q": "Secret"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Secret Student")
        self.assertContains(response, "students")
        self.assertIn("students = Students", provider.calls[0].user_prompt)
        self.assertIn("Never write queries", provider.calls[0].system_prompt)

    def test_ai_written_sql_is_ignored_and_cannot_add_records(self):
        provider = FixedProvider(
            "students; DROP TABLE students; SELECT * FROM users")
        with override_settings(AI_PROVIDER_INSTANCE=provider):
            response = self.client.post(reverse("ai:search"), {"q": "Secret"})
        self.assertContains(response, "Secret Student")
        self.assertNotContains(response, "DROP TABLE")
        self.assertNotContains(response, "SELECT *")

    def test_other_institutions_records_are_never_returned(self):
        provider = FixedProvider("students")
        with override_settings(AI_PROVIDER_INSTANCE=provider):
            response = self.client.post(reverse("ai:search"), {"q": "Foreign"})
        self.assertNotContains(response, "Foreign Student")
        self.assertContains(response, "Nothing matched")

    def test_unknown_record_code_returns_no_results(self):
        provider = FixedProvider("bank_accounts")
        with override_settings(AI_PROVIDER_INSTANCE=provider):
            response = self.client.post(reverse("ai:search"), {"q": "Secret"})
        self.assertContains(response, "Name a record type")
        self.assertNotContains(response, "Secret Student")


class FeatureAccessTests(FeatureBase):
    def test_all_five_screens_are_reachable(self):
        for name in ("help", "draft", "translation", "insights", "search"):
            with self.subTest(name=name):
                response = self.client.get(reverse(f"ai:{name}"))
                self.assertEqual(response.status_code, 200)

    def test_a_user_without_ai_permission_cannot_open_any_screen(self):
        with use_institution(self.school):
            lecturer = User.objects.create_user(
                "feature-lecturer", password="pw123456", role="lecturer",
                institution=self.school, is_staff=True)
        self.client.force_login(lecturer)
        for name in ("help", "draft", "translation", "insights", "search"):
            with self.subTest(name=name):
                response = self.client.post(reverse(f"ai:{name}"), {"q": "test"})
                self.assertEqual(response.status_code, 403)
