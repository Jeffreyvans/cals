"""Privacy boundary tests: nothing protected or foreign reaches a provider."""

from django.core.cache import cache
from django.test import TestCase

from ai.features import Feature
from ai.models import AIUsageLog
from ai.providers import MockProvider
from ai.service import AIRequest, ask_ai_for_user
from ai.tests.helpers import accept, make_school, open_platform, switch_on


class PrivacyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.school, cls.admin = make_school("Privacy School", "privacy-school")
        cls.other, cls.foreign_admin = make_school("Foreign School", "foreign-school")

    def setUp(self):
        cache.clear()
        open_platform()
        switch_on(self.school)
        accept(self.admin, self.school, Feature.HELP)

    def call(self, prompt, *, system="system", names=(), institution=None, provider=None):
        return ask_ai_for_user(
            AIRequest(Feature.HELP, system, prompt),
            self.admin,
            institution=institution,
            names=names,
            provider=provider or MockProvider(["draft"]),
        )

    def test_personal_details_are_redacted_before_the_provider_sees_them(self):
        prompt = (
            "Name: Tapiwa\n"
            "ID 12-345678A12\n"
            "Phone 0771234567\n"
            "Email tapiwa@example.com\n"
            "12 Beech Street, Harare"
        )
        provider = MockProvider(["draft"])
        result = self.call(prompt, names=("Tapiwa",), provider=provider)
        self.assertTrue(result.ok)
        self.assertEqual(len(provider.calls), 1)
        sent = provider.calls[0].user_prompt
        for value in ("Tapiwa", "12-345678A12", "0771234567",
                      "tapiwa@example.com", "Beech Street"):
            self.assertNotIn(value, sent)
        for placeholder in ("[name]", "[id]", "[phone]", "[email]", "[address]"):
            self.assertIn(placeholder, sent)

    def test_health_and_counselling_prompts_are_refused_before_a_call(self):
        provider = MockProvider(["draft"])
        result = self.call(
            "The student's HIV result and counselling session notes are attached.",
            provider=provider,
        )
        self.assertFalse(result.ok)
        self.assertIn("health, counselling", result.error)
        self.assertEqual(provider.calls, [])
        log = AIUsageLog.all_objects.get(institution=self.school)
        self.assertEqual(log.status, "blocked")
        for value in ("HIV", "counselling", "student"):
            self.assertNotIn(value, log.prompt_summary)

    def test_health_terms_in_a_system_prompt_are_also_refused(self):
        provider = MockProvider(["draft"])
        result = self.call("Write a comment", system="health module instructions",
                           provider=provider)
        self.assertFalse(result.ok)
        self.assertEqual(provider.calls, [])

    def test_another_school_name_or_slug_is_never_sent(self):
        provider = MockProvider(["draft"])
        result = self.call(
            "Compare this with Foreign School (foreign-school).", provider=provider
        )
        self.assertFalse(result.ok)
        self.assertIn("another school", result.error)
        self.assertEqual(provider.calls, [])
        log = AIUsageLog.all_objects.get(institution=self.school)
        self.assertNotIn("Foreign School", log.prompt_summary)
        self.assertNotIn("foreign-school", log.prompt_summary)

    def test_caller_cannot_choose_another_institution(self):
        provider = MockProvider(["draft"])
        result = self.call(
            "Write a comment", institution=self.other, provider=provider
        )
        self.assertFalse(result.ok)
        self.assertIn("own institution", result.error)
        self.assertEqual(provider.calls, [])
        self.assertFalse(AIUsageLog.all_objects.filter(institution=self.other).exists())
        log = AIUsageLog.all_objects.get(institution=self.school)
        self.assertEqual(log.status, "blocked")
        self.assertNotIn("Foreign School", log.prompt_summary)

    def test_own_school_name_is_allowed(self):
        provider = MockProvider(["draft"])
        result = self.call("At Privacy School, write a comment.", provider=provider)
        self.assertTrue(result.ok)
        self.assertEqual(len(provider.calls), 1)

    def test_summary_is_short_and_never_holds_full_personal_content(self):
        prompt = "Name: Tapiwa, email tapiwa@example.com, phone 0771234567. " * 10
        result = self.call(prompt, names=("Tapiwa",))
        self.assertTrue(result.ok)
        log = AIUsageLog.all_objects.get(institution=self.school)
        self.assertLessEqual(len(log.prompt_summary), 180)
        for value in ("Tapiwa", "tapiwa@example.com", "0771234567"):
            self.assertNotIn(value, log.prompt_summary)
