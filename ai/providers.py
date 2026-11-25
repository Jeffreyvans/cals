"""Server-side provider interface for optional AI assistance.

This module is deliberately free of Django models and Celery. A provider makes
one short HTTPS request and returns text plus token counts; every decision
about controls, redaction and permissions happens outside it.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from django.conf import settings

TIMEOUT_SECONDS = 15
ANTHROPIC_MESSAGES_PATH = "/v1/messages"
API_BASE_ENV = "AI_API_URL"
KEY_ENV = "AI_API_KEY"
MODEL_ENV = {"fast": "AI_MODEL_FAST", "smart": "AI_MODEL_SMART"}


class AIError(Exception):
    """Base class for errors that are safe to show to a user."""

    user_message = "AI assistance is temporarily unavailable. Please try again later."


class AIConfigurationError(AIError):
    user_message = (
        "AI assistance is not configured for this installation. "
        "Ask your administrator to check the AI settings."
    )


class AITimeoutError(AIError):
    user_message = (
        "The AI service took too long to answer. Nothing was changed; please try again."
    )


class AIProviderError(AIError):
    user_message = (
        "The AI service is unavailable right now. Nothing was changed; please try again."
    )


@dataclass(frozen=True)
class CompletionRequest:
    """The only information a provider receives."""

    system_prompt: str
    user_prompt: str
    max_tokens: int = 600
    tier: str = "fast"


@dataclass(frozen=True)
class Completion:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""


class AIProvider(Protocol):
    def complete(self, request: CompletionRequest) -> Completion:
        """Return one completion or raise an :class:`AIError`."""


class AnthropicProvider:
    """Anthropic Messages API implementation using only the standard library."""

    def __init__(self, urlopen=None):
        self._urlopen = urlopen or urllib.request.urlopen

    def complete(self, request: CompletionRequest) -> Completion:
        key = os.environ.get(KEY_ENV, "").strip()
        if not key:
            raise AIConfigurationError(f"{KEY_ENV} is missing.")
        model_var = MODEL_ENV.get(request.tier)
        if model_var is None:
            raise AIConfigurationError(f"Unknown AI tier: {request.tier}.")
        model = os.environ.get(model_var, "").strip()
        if not model:
            raise AIConfigurationError(f"{model_var} is missing.")

        base = os.environ.get(API_BASE_ENV, "https://api.anthropic.com").rstrip("/")
        payload = {
            "model": model,
            "max_tokens": request.max_tokens,
            "temperature": 0,
            "system": request.system_prompt,
            "messages": [{"role": "user", "content": request.user_prompt}],
        }
        http_request = urllib.request.Request(
            base + ANTHROPIC_MESSAGES_PATH,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "anthropic-version": "2023-06-01",
                "x-api-key": key,
            },
            method="POST",
        )
        timeout = getattr(settings, "AI_TIMEOUT_SECONDS", TIMEOUT_SECONDS)
        try:
            with self._urlopen(http_request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
        except TimeoutError as exc:
            raise AITimeoutError(str(exc)) from exc
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise AIConfigurationError(f"AI API rejected the key (HTTP {exc.code}).") from exc
            raise AIProviderError(f"AI API returned HTTP {exc.code}.") from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, TimeoutError):
                raise AITimeoutError(str(reason)) from reason
            raise AIProviderError("The AI service could not be reached.") from exc
        except OSError as exc:
            raise AIProviderError("The AI service could not be reached.") from exc

        try:
            data = json.loads(body)
        except (ValueError, UnicodeError) as exc:
            raise AIProviderError("The AI service returned an invalid response.") from exc
        return self._completion_from(data, model)

    @staticmethod
    def _completion_from(data, model: str) -> Completion:
        if not isinstance(data, dict):
            raise AIProviderError("The AI service returned an invalid response.")
        blocks = data.get("content")
        text = ""
        if isinstance(blocks, list):
            text = "\n".join(
                str(block.get("text", ""))
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "text"
            ).strip()
        if not text:
            raise AIProviderError("The AI service returned no usable text.")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return Completion(
            text=text,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            model=str(data.get("model") or model),
        )


class MockProvider:
    """Deterministic in-process provider for tests. Never used in production."""

    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls: list[CompletionRequest] = []
        self._next = 0

    def complete(self, request: CompletionRequest) -> Completion:
        self.calls.append(request)
        if self._next < len(self.responses):
            item = self.responses[self._next]
            self._next += 1
        else:
            item = "Mock AI answer."
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, Completion):
            return item
        return Completion(text=str(item), model="mock")


def provider_for(tier: str = "fast") -> AIProvider:
    """Return the configured provider, allowing tests to inject one safely."""
    injected = getattr(settings, "AI_PROVIDER_INSTANCE", None)
    if injected is not None:
        return injected
    if getattr(settings, "AI_PROVIDER", "anthropic") == "mock":
        return MockProvider()
    return AnthropicProvider()
