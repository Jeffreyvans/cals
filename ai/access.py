"""The gates every AI feature must pass before a provider call is made."""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Q

from core.access import user_has_code

from .features import ALL_FEATURES, CONSENT_VERSION, Feature
from .models import AIConsent, AIFeatureSetting, AIPlatformSetting


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str = ""
    setting: AIFeatureSetting | None = None


def resolve_institution(user, institution=None):
    """Use the supplied institution, otherwise the signed-in user's own."""
    if institution is not None:
        return institution
    return getattr(user, "institution", None)


def platform_ai_enabled():
    """The platform kill switch. Missing configuration means closed."""
    return AIPlatformSetting.load().enabled


def check_access(user, feature, institution=None):
    """Return a single deny/allow decision with a message safe to display."""
    if not getattr(user, "is_authenticated", False):
        return AccessDecision(False, "Please sign in before using AI assistance.")
    inst = resolve_institution(user, institution)
    if inst is None:
        return AccessDecision(False, "Your account is not linked to an institution.")
    if not platform_ai_enabled():
        return AccessDecision(
            False,
            "AI assistance is switched off for the whole platform. "
            "Nothing was sent and nothing was changed.",
        )
    if feature not in Feature.values:
        return AccessDecision(False, "That AI feature is not available.")
    setting = AIFeatureSetting.all_objects.filter(institution=inst, feature=feature).first()
    if setting is None or not setting.enabled:
        return AccessDecision(
            False,
            "This AI feature is not switched on for your institution. "
            "Ask your administrator to enable it.",
        )
    if not user_has_code(user, "ai.use"):
        return AccessDecision(False, "You do not have permission to use AI assistance.")
    consent = (
        AIConsent.all_objects.filter(
            institution=inst,
            accepted_by=user,
            version=CONSENT_VERSION,
            withdrawn_at__isnull=True,
        )
        .filter(Q(feature=feature) | Q(feature=ALL_FEATURES))
        .order_by("-accepted_at", "-pk")
        .first()
    )
    if consent is None:
        return AccessDecision(
            False,
            "Please accept the AI consent screen before using this feature. "
            "Nothing was sent.",
        )
    return AccessDecision(True, setting=setting)
