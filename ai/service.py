"""The single entry point for server-side AI calls.

Views must call :func:`ask_ai` rather than a provider directly. This keeps the
call on the server and gives one place to add the controls, redaction, usage
limits and logging in later parts of the AI work.
"""

from __future__ import annotations

from dataclasses import dataclass

from .access import check_access, resolve_institution
from .privacy import PrivacyError, check_prompt_privacy
from .providers import (
    AIError,
    AIProvider,
    CompletionRequest,
    provider_for,
)
from .redaction import redact_text, safe_summary
from .usage import limit_error, record_usage, usage_counts


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


def ask_ai_for_user(
    request: AIRequest,
    user,
    institution=None,
    *,
    provider: AIProvider | None = None,
    names=(),
) -> AIResult:
    """Check every control, then call the provider and log the outcome.

    Views must use this function rather than :func:`ask_ai`. A blocked call
    writes a zero-token log row for monitoring, but never reaches the provider.
    """
    # Log against the signed-in person's own school even when a caller supplies
    # a different one. The access check refuses that call before any provider
    # request, and no foreign identifier is copied into the log.
    inst = getattr(user, "institution", None) or resolve_institution(user, institution)
    decision = check_access(user, request.feature, institution)
    blocked_summary = "Blocked before sending; nothing was shared."
    if not decision.allowed:
        record_usage(
            user, inst, request.feature,
            status="blocked", tier=request.tier, prompt_summary=blocked_summary,
        )
        return AIResult(ok=False, error=decision.reason)
    error = limit_error(decision.setting, usage_counts(inst, user))
    if error:
        record_usage(
            user, inst, request.feature,
            status="blocked", tier=request.tier, prompt_summary=blocked_summary,
        )
        return AIResult(ok=False, error=error)

    # Redact before the provider sees anything, then refuse protected or other
    # school content. The privacy check uses exactly the text that will be sent.
    redacted = AIRequest(
        feature=request.feature,
        system_prompt=redact_text(request.system_prompt, names),
        user_prompt=redact_text(request.user_prompt, names),
        tier=request.tier,
        max_tokens=request.max_tokens,
    )
    try:
        check_prompt_privacy(f"{redacted.system_prompt}\n{redacted.user_prompt}", inst)
    except PrivacyError as exc:
        record_usage(
            user, inst, request.feature,
            status="blocked", tier=request.tier, prompt_summary=blocked_summary,
        )
        return AIResult(ok=False, error=exc.user_message)

    result = ask_ai(redacted, provider=provider)
    record_usage(
        user,
        inst,
        request.feature,
        status="success" if result.ok else "failure",
        model_name=result.model,
        tier=request.tier,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        prompt_summary=safe_summary(request.user_prompt, names),
    )
    return result
