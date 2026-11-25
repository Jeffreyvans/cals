from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from ai.access import check_access
from ai.features import ALL_FEATURES, CONSENT_VERSION, Feature, disclosure_for
from ai.models import AIConsent, AIFeatureSetting, AIPlatformSetting, AIUsageLog
from ai.providers import Completion, MockProvider
from ai.service import AIRequest, ask_ai_for_user
from ai.usage import record_usage
from core.models import AuditEntry, Institution
from core.roles import ensure_roles
from core.tenancy import use_institution

User = get_user_model()


def open_platform():
    setting = AIPlatformSetting.load()
    setting.enabled = True
    setting.save()
    return setting


def switch_on(institution, feature=Feature.HELP, **limits):
    defaults = {
        "enabled": True,
        "institution_daily_limit": 100,
        "institution_monthly_limit": 1000,
        "user_daily_limit": 20,
        "user_monthly_limit": 200,
    }
    defaults.update(limits)
    setting, _ = AIFeatureSetting.all_objects.update_or_create(
        institution=institution, feature=feature, defaults=defaults
    )
    return setting


def accept(user, institution, feature=ALL_FEATURES):
    disclosure = disclosure_for(feature)
    return AIConsent.all_objects.create(
        institution=institution,
        accepted_by=user,
        feature=feature,
        version=CONSENT_VERSION,
        shared_categories=disclosure["shared"],
        recorded_categories=["User id, feature, tokens, cost estimate, time and summary"],
        purpose=disclosure["purpose"],
    )


class ControlTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.school = Institution.objects.create(
            name="Control School", slug="control-school", type="secondary"
        )
        cls.other = Institution.objects.create(
            name="Other School", slug="other-school", type="secondary"
        )
        with use_institution(cls.school):
            ensure_roles(cls.school)
            cls.admin = User.objects.create_user(
                "ai-control-admin", password="pw123456", role="admin",
                is_staff=True, is_superuser=True, institution=cls.school,
            )
            cls.teacher = User.objects.create_user(
                "ai-control-teacher", password="pw123456", role="lecturer",
                institution=cls.school,
            )
        with use_institution(cls.other):
            ensure_roles(cls.other)
            cls.foreign_admin = User.objects.create_user(
                "ai-control-foreign", password="pw123456", role="admin",
                is_staff=True, is_superuser=True, institution=cls.other,
            )

    def call(self, user=None, provider=None, prompt="Write a comment"):
        user = user or self.admin
        return ask_ai_for_user(
            AIRequest("help", "system", prompt),
            user,
            provider=provider or MockProvider(["draft"]),
        )

    def test_everything_is_off_by_default(self):
        decision = check_access(self.admin, Feature.HELP, self.school)
        self.assertFalse(decision.allowed)
        self.assertIn("whole platform", decision.reason)
        provider = MockProvider()
        result = self.call(provider=provider)
        self.assertFalse(result.ok)
        self.assertEqual(provider.calls, [])
        self.assertEqual(AIUsageLog.all_objects.count(), 1)

    def test_platform_kill_switch_overrides_institution_switches(self):
        switch_on(self.school)
        accept(self.admin, self.school)
        decision = check_access(self.admin, Feature.HELP, self.school)
        self.assertFalse(decision.allowed)
        open_platform()
        self.assertTrue(check_access(self.admin, Feature.HELP, self.school).allowed)

    def test_switches_are_per_feature_and_per_institution(self):
        open_platform()
        switch_on(self.school, Feature.HELP)
        accept(self.admin, self.school)
        self.assertTrue(check_access(self.admin, Feature.HELP, self.school).allowed)
        self.assertFalse(check_access(self.admin, Feature.DRAFT, self.school).allowed)
        self.assertFalse(
            check_access(self.foreign_admin, Feature.HELP, self.other).allowed
        )

    def test_consent_is_required_and_records_who_what_and_when(self):
        open_platform()
        switch_on(self.school)
        self.assertFalse(check_access(self.admin, Feature.HELP, self.school).allowed)
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("ai:consent"), {"feature": Feature.HELP, "accept": "yes"}
        )
        self.assertEqual(response.status_code, 302)
        record = AIConsent.all_objects.filter(institution=self.school).get()
        self.assertEqual(record.accepted_by, self.admin)
        self.assertEqual(record.version, CONSENT_VERSION)
        self.assertEqual(record.feature, Feature.HELP)
        self.assertTrue(record.shared_categories)
        self.assertTrue(record.recorded_categories)
        self.assertIsNotNone(record.accepted_at)
        self.assertTrue(check_access(self.admin, Feature.HELP, self.school).allowed)

    def test_consent_can_be_withdrawn(self):
        open_platform()
        switch_on(self.school)
        accept(self.admin, self.school)
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("ai:consent"), {"action": "withdraw", "feature": ALL_FEATURES}
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(check_access(self.admin, Feature.HELP, self.school).allowed)

    def test_permission_is_required(self):
        open_platform()
        switch_on(self.school)
        accept(self.teacher, self.school)
        decision = check_access(self.teacher, Feature.HELP, self.school)
        self.assertFalse(decision.allowed)
        self.assertIn("permission", decision.reason)

    def test_daily_and_monthly_limits_block_before_a_provider_call(self):
        open_platform()
        switch_on(
            self.school,
            institution_daily_limit=2,
            institution_monthly_limit=3,
            user_daily_limit=5,
            user_monthly_limit=5,
        )
        accept(self.admin, self.school)
        provider = MockProvider(["one", "two"])
        self.assertTrue(self.call(provider=provider).ok)
        self.assertTrue(self.call(provider=provider).ok)
        blocked = self.call(provider=provider)
        self.assertFalse(blocked.ok)
        self.assertIn("institution has reached today", blocked.error)
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(
            AIUsageLog.all_objects.filter(institution=self.school, status="blocked").count(), 1
        )

    def test_user_limit_is_separate_from_institution_limit(self):
        open_platform()
        switch_on(self.school, institution_daily_limit=50, user_daily_limit=1)
        accept(self.teacher, self.school)
        # A direct permission grant is only used to isolate the limit being tested.
        self.teacher._permission_codes = {"ai.use"}
        self.assertTrue(self.call(user=self.teacher).ok)
        self.assertFalse(self.call(user=self.teacher).ok)

    def test_log_has_redacted_summary_and_estimated_cost(self):
        open_platform()
        switch_on(self.school)
        accept(self.admin, self.school)
        with patch.dict(
            "os.environ",
            {
                "AI_COST_PER_MILLION_INPUT": "3",
                "AI_COST_PER_MILLION_OUTPUT": "6",
            },
        ):
            result = ask_ai_for_user(
                AIRequest(
                    "help", "system",
                    "Name: Tapiwa, email tapiwa@example.com, phone 0771234567",
                ),
                self.admin,
                provider=MockProvider(
                    [Completion("answer", input_tokens=1_000_000, output_tokens=500_000)]
                ),
            )
        self.assertTrue(result.ok)
        log = AIUsageLog.all_objects.get(institution=self.school)
        self.assertEqual(log.user, self.admin)
        self.assertEqual(log.feature, "help")
        self.assertEqual(log.status, "success")
        self.assertEqual(log.input_tokens + log.output_tokens, 1_500_000)
        self.assertEqual(log.cost_estimate, Decimal("6.000000"))
        self.assertNotIn("Tapiwa", log.prompt_summary)
        self.assertNotIn("tapiwa@example.com", log.prompt_summary)
        self.assertNotIn("0771234567", log.prompt_summary)

    def test_usage_page_shows_counts_and_limits(self):
        open_platform()
        setting = switch_on(self.school, user_daily_limit=7)
        accept(self.admin, self.school)
        record_usage(self.admin, self.school, "help", prompt_summary="safe summary")
        self.client.force_login(self.admin)
        response = self.client.get(reverse("ai:usage"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Today")
        self.assertContains(response, "safe summary")
        self.assertContains(response, str(setting.user_daily_limit))

    def test_settings_page_switches_a_feature_and_saves_limits(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("ai:settings"),
            {"action": "toggle", "feature": "draft", "enabled": "on"},
        )
        self.assertEqual(response.status_code, 302)
        setting = AIFeatureSetting.all_objects.get(institution=self.school, feature="draft")
        self.assertTrue(setting.enabled)
        response = self.client.post(
            reverse("ai:settings"),
            {
                "action": "limits", "feature": "draft",
                "institution_daily_limit": "12", "institution_monthly_limit": "120",
                "user_daily_limit": "2", "user_monthly_limit": "20",
            },
        )
        self.assertEqual(response.status_code, 302)
        setting.refresh_from_db()
        self.assertEqual(setting.institution_daily_limit, 12)
        self.assertEqual(setting.user_daily_limit, 2)
        self.assertTrue(
            AuditEntry.all_objects.filter(institution=self.school, action="ai.feature_setting_changed").exists()
        )

    def test_unknown_feature_cannot_be_switched(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse("ai:settings"),
            {"action": "toggle", "feature": "delete_everything", "enabled": "on"},
        )
        self.assertFalse(
            AIFeatureSetting.all_objects.filter(feature="delete_everything").exists()
        )

    def test_platform_kill_switch_starts_closed(self):
        setting = AIPlatformSetting.load()
        self.assertFalse(setting.enabled)
        self.assertFalse(check_access(self.admin, Feature.HELP, self.school).allowed)
