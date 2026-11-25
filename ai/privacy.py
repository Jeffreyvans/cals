"""Refuse prompts that mention protected information or another institution.

The rule is deliberately simple: if a prompt mentions health or counselling,
or names a school other than the one the signed-in person belongs to, the text
is not sent at all. A false positive only loses an AI answer; a false negative
would disclose something a school never agreed to share.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.models import Institution

HEALTH_WORDS = (
    "health",
    "medical",
    "medicine",
    "medication",
    "prescription",
    "diagnosis",
    "diagnosed",
    "patient",
    "clinic",
    "hospital",
    "HIV",
    "AIDS",
    "pregnan",
    "therapy",
    "therapist",
    "psychologist",
    "psychiatrist",
    "psychiatric",
    "depression",
    "anxiety",
    "suicide",
    "self-harm",
    "trauma",
    "abuse",
)
COUNSELLING_WORDS = (
    "counsell",
    "counsel",
    "session note",
    "case note",
    "confidential note",
)


def _pattern(words):
    return re.compile(r"\b(?:" + "|".join(re.escape(word) for word in words) + r")", re.I)


HEALTH_RE = _pattern(HEALTH_WORDS)
COUNSELLING_RE = _pattern(COUNSELLING_WORDS)


@dataclass(frozen=True)
class PrivacyError(Exception):
    """A prompt was refused locally. ``user_message`` is safe to display."""

    user_message: str = (
        "This request mentions health, counselling or another school, so nothing "
        "was sent. Please remove those details and try again."
    )

    def __str__(self):
        return self.user_message


def protected_categories(text):
    """Return the protected categories mentioned in ``text``."""
    categories = []
    if HEALTH_RE.search(text or ""):
        categories.append("health")
    if COUNSELLING_RE.search(text or ""):
        categories.append("counselling")
    return tuple(categories)


def foreign_institution_markers(text, institution):
    """Return other schools named in ``text``; the caller's own school is fine."""
    if institution is None:
        return ()
    markers = []
    others = Institution.objects.exclude(pk=institution.pk).values_list("name", "slug")
    for name, slug in others:
        for value in (name, slug):
            value = (value or "").strip()
            if len(value) < 3:
                continue
            if re.search(r"\b" + re.escape(value) + r"\b", text or "", re.I):
                if value not in markers:
                    markers.append(value)
    return tuple(markers)


def check_prompt_privacy(text, institution):
    """Raise :class:`PrivacyError` before a disallowed prompt can be sent."""
    if protected_categories(text) or foreign_institution_markers(text, institution):
        raise PrivacyError()
