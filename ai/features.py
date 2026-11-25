"""Feature codes and the consent disclosure shown for each one."""

from django.db import models

ALL_FEATURES = "*"
CONSENT_VERSION = "2026-11-25"


class Feature(models.TextChoices):
    HELP = "help", "Help assistant"
    DRAFT = "draft", "Draft writing"
    TRANSLATION = "translation", "Translation"
    INSIGHTS = "insights", "Dashboard insights"
    SEARCH = "search", "Natural-language search"


FEATURE_CODES = [code for code, _label in Feature.choices]
FEATURE_LABELS = {code: label for code, label in Feature.choices}
CONSENT_TARGETS = [(ALL_FEATURES, "All AI features")] + [
    (code, label) for code, label in Feature.choices
]

# What the consent screen must say before anybody can use a feature. These
# strings are also stored as a snapshot on the acceptance record.
FEATURE_DISCLOSURES = {
    Feature.HELP: {
        "shared": [
            "Your question after names, ID numbers, phone numbers, email addresses and "
            "street addresses have been removed.",
            "The titles of in-app guides selected locally as possible sources.",
        ],
        "purpose": "Answer questions using only the in-app guides and documentation.",
    },
    Feature.DRAFT: {
        "shared": [
            "The writing instructions you type and any draft text you paste, after redaction.",
            "The document type (report-card comment, announcement, fee reminder or letter).",
        ],
        "purpose": "Produce an editable draft for a person to review and approve.",
    },
    Feature.TRANSLATION: {
        "shared": ["The text you ask to translate, after redaction."],
        "purpose": "Translate short text between English, Shona and Ndebele.",
    },
    Feature.INSIGHTS: {
        "shared": [
            "Only labelled database totals, percentages and currency amounts.",
            "No names, student records, contact details or record-level details.",
        ],
        "purpose": "Wrap already-calculated figures in plain language.",
    },
    Feature.SEARCH: {
        "shared": [
            "Your redacted search request and the fixed list of field labels it may choose from.",
            "No SQL, no record values and no documents.",
        ],
        "purpose": "Turn a request into a permission-checked filter.",
    },
}

RECORDED_ALWAYS = [
    "Your user id and your institution id.",
    "The feature used, the time, token counts and a cost estimate.",
    "A short, redacted summary of the prompt. Full prompts and answers are not stored.",
    "The consent version, who accepted it and when it was accepted.",
    "Any bad-answer feedback you choose to submit.",
]

PROVIDER_NAME = "Anthropic Claude"


def disclosure_for(feature):
    """Return the disclosure for a feature, or a combined summary for all."""
    if feature == ALL_FEATURES:
        return {
            "shared": [
                "Only the minimum redacted text or labelled figures each enabled feature needs.",
                "Never health or counselling information, and never a record from another school.",
            ],
            "purpose": "The AI features switched on by your institution.",
        }
    return FEATURE_DISCLOSURES.get(feature, FEATURE_DISCLOSURES[Feature.HELP])
