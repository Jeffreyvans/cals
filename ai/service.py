"""The single entry point for server-side AI calls.

Views must call :func:`ask_ai` rather than a provider directly. This keeps the
call on the server and gives one place to add the controls, redaction, usage
limits and logging in later parts of the AI work.
"""

from __future__ import annotations

from dataclasses import dataclass

from .providers import (
    AIError,
    AIProvider,
    CompletionRequest,
    provider_for,
)


@dataclass(frozen=True)
class AIRequest:
    feature: str
    system_prompt: str
    user_prompt: str
    tier: str = "fast"
    max_tokens: int = 600


@dataclass(frozen=True)
class AIResult:
    ok: bool
    text: str = ""
    error: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def ask_ai(request: AIRequest, provider: AIProvider | None = None) -> AIResult:
    """Make one short call and turn any failure into a safe, user-facing result."""
    provider = provider or provider_for(request.tier)
    completion_request = CompletionRequest(
        system_prompt=request.system_prompt,
        user_prompt=request.user_prompt,
        max_tokens=min(max(1, request.max_tokens), 1200),
        tier=request.tier,
    )
    try:
        completion = provider.complete(completion_request)
    except AIError as exc:
        return AIResult(ok=False, error=exc.user_message)
    except Exception:
        # Never expose provider internals, headers or keys to a page or a log.
        return AIResult(ok=False, error="AI assistance is temporarily unavailable. Please try again later.")
    return AIResult(
        ok=True,
        text=completion.text,
        model=completion.model,
        input_tokens=completion.input_tokens,
        output_tokens=completion.output_tokens,
    )
