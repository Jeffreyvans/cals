import json
import urllib.error
from io import BytesIO
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase

from ai.providers import (
    AIConfigurationError,
    AIProviderError,
    AITimeoutError,
    AnthropicProvider,
    Completion,
    CompletionRequest,
    MockProvider,
    provider_for,
)
from ai.service import AIRequest, ask_ai


class FakeResponse(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
        return False


def api_response(text="Hello", **usage):
    return FakeResponse(
        json.dumps(
            {
                "content": [{"type": "text", "text": text}],
                "usage": usage,
            }
        ).encode("utf-8")
    )


class AnthropicProviderTests(SimpleTestCase):
    def setUp(self):
        self.environment = {
            "AI_API_KEY": "test-key",
            "AI_MODEL_FAST": "env-fast-model",
            "AI_MODEL_SMART": "env-smart-model",
        }
        self.env_patch = patch.dict("os.environ", self.environment, clear=True)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def test_models_and_key_come_from_environment_only(self):
        calls = []

        def urlopen(request, timeout):
            calls.append((request, timeout))
            return api_response(input_tokens=12, output_tokens=3)

        with patch("urllib.request.urlopen", urlopen):
            fast = AnthropicProvider().complete(
                CompletionRequest("system", "prompt", tier="fast")
            )
            smart = AnthropicProvider().complete(
                CompletionRequest("system", "prompt", tier="smart")
            )

        self.assertEqual(fast.model, "env-fast-model")
        self.assertEqual(smart.model, "env-smart-model")
        self.assertEqual((fast.input_tokens, fast.output_tokens), (12, 3))
        self.assertEqual(len(calls), 2)
        for request, timeout in calls:
            payload = json.loads(request.data)
            self.assertEqual(request.get_header("X-api-key"), "test-key")
            self.assertEqual(request.full_url, "https://api.anthropic.com/v1/messages")
            self.assertEqual(payload["max_tokens"], 600)
            self.assertEqual(payload["system"], "system")
            self.assertEqual(payload["messages"], [{"role": "user", "content": "prompt"}])
            self.assertEqual(request.method, "POST")
            self.assertEqual(timeout, 15)

    def test_base_url_can_be_overridden_for_testing(self):
        calls = []

        def urlopen(request, timeout):
            calls.append(request.full_url)
            return api_response()

        with patch.dict("os.environ", {"AI_API_URL": "https://example.test/api/"}):
            with patch("urllib.request.urlopen", urlopen):
                AnthropicProvider().complete(CompletionRequest("s", "p"))
        self.assertEqual(calls, ["https://example.test/api/v1/messages"])

    def test_missing_key_fails_before_any_network_call(self):
        def urlopen(*_args, **_kwargs):
            self.fail("no request should be made without a key")

        with patch.dict("os.environ", {}, clear=True):
            with patch("urllib.request.urlopen", urlopen):
                with self.assertRaises(AIConfigurationError) as caught:
                    AnthropicProvider().complete(CompletionRequest("s", "p"))
        self.assertIn("AI_API_KEY", str(caught.exception))

    def test_missing_model_fails_with_a_clear_message(self):
        with patch.dict("os.environ", {"AI_API_KEY": "test-key"}, clear=True):
            with self.assertRaises(AIConfigurationError) as caught:
                AnthropicProvider().complete(CompletionRequest("s", "p", tier="smart"))
        self.assertIn("AI_MODEL_SMART", str(caught.exception))

    def test_unknown_tier_is_refused(self):
        with self.assertRaises(AIConfigurationError):
            AnthropicProvider().complete(CompletionRequest("s", "p", tier="magic"))

    def test_timeout_has_a_clear_user_message(self):
        def urlopen(*_args, **_kwargs):
            raise TimeoutError("too slow")

        with patch("urllib.request.urlopen", urlopen):
            with self.assertRaises(AITimeoutError) as caught:
                AnthropicProvider().complete(CompletionRequest("s", "p"))
        self.assertIn("too long", caught.exception.user_message)

    def test_dns_failure_is_reported_as_unavailable(self):
        def urlopen(*_args, **_kwargs):
            raise urllib.error.URLError("no route")

        with patch("urllib.request.urlopen", urlopen):
            with self.assertRaises(AIProviderError) as caught:
                AnthropicProvider().complete(CompletionRequest("s", "p"))
        self.assertIn("unavailable", caught.exception.user_message)

    def test_rejected_key_is_a_configuration_error(self):
        error = urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)

        def urlopen(*_args, **_kwargs):
            raise error

        with patch("urllib.request.urlopen", urlopen):
            with self.assertRaises(AIConfigurationError):
                AnthropicProvider().complete(CompletionRequest("s", "p"))

    def test_server_error_is_a_provider_error(self):
        error = urllib.error.HTTPError("url", 503, "Unavailable", {}, None)

        def urlopen(*_args, **_kwargs):
            raise error

        with patch("urllib.request.urlopen", urlopen):
            with self.assertRaises(AIProviderError):
                AnthropicProvider().complete(CompletionRequest("s", "p"))

    def test_empty_or_invalid_response_is_refused(self):
        for body in (b"not json", json.dumps({"content": []}).encode()):
            with self.subTest(body=body):
                with patch(
                    "urllib.request.urlopen",
                    lambda *a, _body=body, **k: FakeResponse(_body),
                ):
                    with self.assertRaises(AIProviderError):
                        AnthropicProvider().complete(CompletionRequest("s", "p"))


class MockProviderTests(SimpleTestCase):
    def test_records_requests_and_returns_queued_completion(self):
        provider = MockProvider(
            [Completion("first", input_tokens=4, output_tokens=2), "second"]
        )
        first = provider.complete(CompletionRequest("s1", "p1", tier="fast"))
        second = provider.complete(CompletionRequest("s2", "p2", tier="smart"))
        self.assertEqual(first.text, "first")
        self.assertEqual(second.text, "second")
        self.assertEqual([call.tier for call in provider.calls], ["fast", "smart"])
        self.assertEqual([call.user_prompt for call in provider.calls], ["p1", "p2"])

    def test_can_raise_configured_failure(self):
        provider = MockProvider([AITimeoutError("slow")])
        with self.assertRaises(AITimeoutError):
            provider.complete(CompletionRequest("s", "p"))

    def test_provider_selection_can_be_overridden_by_settings(self):
        provider = MockProvider(["injected"])
        with patch.object(settings, "AI_PROVIDER_INSTANCE", provider):
            self.assertIs(provider_for("fast"), provider)
        with patch.object(settings, "AI_PROVIDER_INSTANCE", None):
            with patch.object(settings, "AI_PROVIDER", "mock"):
                self.assertIsInstance(provider_for("fast"), MockProvider)


class ServiceTests(SimpleTestCase):
    def test_ai_errors_become_safe_results(self):
        provider = MockProvider(
            [AIConfigurationError("secret key detail"), AITimeoutError("host detail")]
        )
        first = ask_ai(
            AIRequest("help", "system", "prompt"), provider=provider
        )
        second = ask_ai(
            AIRequest("help", "system", "prompt"), provider=provider
        )
        self.assertFalse(first.ok)
        self.assertNotIn("secret", first.error)
        self.assertIn("not configured", first.error)
        self.assertFalse(second.ok)
        self.assertIn("too long", second.error)

    def test_unexpected_provider_exception_does_not_leak_details(self):
        class BrokenProvider:
            def complete(self, _request):
                raise RuntimeError("x-api-key=super-secret")

        result = ask_ai(AIRequest("help", "s", "p"), provider=BrokenProvider())
        self.assertFalse(result.ok)
        self.assertNotIn("super-secret", result.error)

    def test_successful_result_counts_tokens(self):
        provider = MockProvider([Completion("answer", input_tokens=7, output_tokens=5)])
        result = ask_ai(AIRequest("help", "s", "p"), provider=provider)
        self.assertTrue(result.ok)
        self.assertEqual(result.text, "answer")
        self.assertEqual(result.total_tokens, 12)

    def test_max_tokens_is_bounded(self):
        provider = MockProvider()
        ask_ai(AIRequest("help", "s", "p", max_tokens=99999), provider=provider)
        self.assertEqual(provider.calls[0].max_tokens, 1200)

    def test_provider_module_has_no_celery_dependency(self):
        from ai import providers

        source = open(providers.__file__, encoding="utf-8").read().lower()
        self.assertNotIn("import celery", source)
        self.assertNotIn("from celery", source)
