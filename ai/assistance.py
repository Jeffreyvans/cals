"""The five optional AI features built on the guarded service boundary.

Every feature in this module calls :func:`ai.service.ask_ai_for_user`; none
calls a provider directly and none writes a business record. Views only render
what these functions return.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from core.access import user_has_any_code, user_has_code
from core.help import GUIDES, guide_for
from core.search import SEARCH_CODES, SEARCH_TYPES, search_groups

from .features import Feature
from .output import sanitize_output
from .privacy import protected_categories
from .safety import (
    DRAFT_UNSAFE_MESSAGE,
    names_for,
    output_has_injection,
    unsafe_draft,
)
from .service import AIRequest, ask_ai_for_user

DRAFT_LABEL = "AI draft, please review"
REVIEW_LABEL = "needs review"
DONT_KNOW = "I don't know."

DRAFT_KINDS = {
    "report_card": (
        "Report-card comment",
        "Write a short, factual comment about a learner's progress.",
    ),
    "announcement": (
        "Announcement",
        "Write a short announcement for a school community.",
    ),
    "fee_reminder": (
        "Fee reminder",
        "Write a polite reminder about an outstanding fee balance.",
    ),
    "letter": (
        "Letter",
        "Write a short formal letter from a school office.",
    ),
}
LANGUAGES = {"en": "English", "sn": "Shona", "nd": "Ndebele"}
MAX_INPUT = 1500
STOPWORDS = {
    "about", "again", "can", "could", "does", "find", "from", "have", "how",
    "into", "make", "open", "please", "show", "some", "that", "the", "their",
    "them", "this", "where", "which", "with", "would", "your",
}


@dataclass(frozen=True)
class HelpAnswer:
    ok: bool
    text: str = ""
    error: str = ""
    sources: tuple = ()
    log_id: int = 0


@dataclass(frozen=True)
class DraftAnswer:
    ok: bool
    text: str = ""
    error: str = ""
    kind: str = ""
    log_id: int = 0


@dataclass(frozen=True)
class TranslationAnswer:
    ok: bool
    text: str = ""
    error: str = ""
    source: str = ""
    target: str = ""
    log_id: int = 0


@dataclass(frozen=True)
class InsightAnswer:
    ok: bool
    text: str = ""
    error: str = ""
    figures: tuple = ()
    reviewed: bool = True
    log_id: int = 0


@dataclass(frozen=True)
class SearchAnswer:
    ok: bool
    text: str = ""
    error: str = ""
    chosen: tuple = ()
    groups: tuple = ()
    log_id: int = 0


def _keywords(text):
    return {
        word for word in re.findall(r"[A-Za-z]{4,}", text or "")
        if word.lower() not in STOPWORDS
    }


def _help_sources(user):
    """Locally resolved guide entries, after removing protected content."""
    guide = GUIDES[guide_for(user)]
    sources = []
    seen = set()
    for label, url_name in guide["tasks"]:
        # A guide entry about a protected service is never used as a source.
        if protected_categories(label):
            continue
        try:
            from django.urls import reverse

            url = reverse(url_name)
        except Exception:
            continue
        if label in seen:
            continue
        seen.add(label)
        sources.append({"label": label, "url": url})
    return guide["title"], sources


def help_answer(user, question, *, institution=None, provider=None, http_request=None):
    """Answer only from the signed-in person's in-app guide, with local links."""
    question = str(question or "").strip()[:MAX_INPUT]
    if len(question) < 3:
        return HelpAnswer(False, error="Type a question about how Vanpere works.")
    title, sources = _help_sources(user)
    matches = [
        source for source in sources
        if _keywords(question) & _keywords(source["label"])
    ]
    if not matches:
        return HelpAnswer(True, text=DONT_KNOW)
    lines = [f"Guide: {title}"] + [f"Source: {item['label']}" for item in matches]
    result = ask_ai_for_user(
        AIRequest(
            feature=Feature.HELP,
            system_prompt=(
                "Answer only from the listed in-app guide source titles. "
                "If they do not support an answer, reply exactly: I don't know. "
                "Do not add facts, links or commands."
            ),
            user_prompt="\n".join(lines) + f"\nQuestion: {question}",
            tier="fast",
            max_tokens=260,
        ),
        user,
        institution,
        provider=provider,
        names=names_for(user, institution),
        http_request=http_request,
    )
    if not result.ok:
        return HelpAnswer(False, error=result.error, log_id=result.log_id)
    text = sanitize_output(result.text, limit=700)
    supported = _keywords(text) & set().union(
        *(_keywords(item["label"]) for item in matches)
    )
    if not text or not supported or output_has_injection(result.text):
        return HelpAnswer(True, text=DONT_KNOW, log_id=result.log_id)
    return HelpAnswer(
        True, text=text, sources=tuple(matches), log_id=result.log_id
    )


def make_draft(user, kind, instructions, *, institution=None, provider=None,
               http_request=None):
    """Return a preview-only draft. No record is written and nothing is sent."""
    if kind not in DRAFT_KINDS:
        return DraftAnswer(False, error="Choose a valid document type.")
    instructions = str(instructions or "").strip()[:MAX_INPUT]
    if len(instructions) < 3:
        return DraftAnswer(False, error="Describe what the draft should say.")
    label, guidance = DRAFT_KINDS[kind]
    result = ask_ai_for_user(
        AIRequest(
            feature=Feature.DRAFT,
            system_prompt=(
                f"Write one short, editable draft for a {label}. {guidance} "
                "Return only the draft text. Do not include links or commands."
            ),
            user_prompt=f"Document type: {label}\nInstructions: {instructions}",
            tier="smart",
            max_tokens=600,
        ),
        user,
        institution,
        provider=provider,
        names=names_for(user, institution),
        http_request=http_request,
    )
    if not result.ok:
        return DraftAnswer(False, error=result.error, log_id=result.log_id)
    if output_has_injection(result.text):
        return DraftAnswer(
            False,
            error="The AI response included instructions, so nothing was shown. Please try again.",
            log_id=result.log_id,
        )
    text = sanitize_output(result.text, limit=2000)
    if not text:
        return DraftAnswer(
            False, error="The AI service returned no usable draft.", log_id=result.log_id
        )
    if unsafe_draft(text):
        return DraftAnswer(
            False, error=DRAFT_UNSAFE_MESSAGE, log_id=result.log_id
        )
    return DraftAnswer(True, text=text, kind=kind, log_id=result.log_id)


def translate_text(user, source, target, text, *, institution=None, provider=None,
                   http_request=None):
    """Translate a short passage between the three supported languages."""
    if source not in LANGUAGES or target not in LANGUAGES:
        return TranslationAnswer(False, error="Choose a supported language.")
    text = str(text or "").strip()[:MAX_INPUT]
    if not text:
        return TranslationAnswer(False, error="Type the text to translate.")
    result = ask_ai_for_user(
        AIRequest(
            feature=Feature.TRANSLATION,
            system_prompt=(
                f"Translate the supplied text from {LANGUAGES[source]} into "
                f"{LANGUAGES[target]}. Return only the translation. "
                "Do not include links or commands."
            ),
            user_prompt=f"Text to translate: {text}",
            tier="fast",
            max_tokens=600,
        ),
        user,
        institution,
        provider=provider,
        names=names_for(user, institution),
        http_request=http_request,
    )
    if not result.ok:
        return TranslationAnswer(False, error=result.error, log_id=result.log_id)
    if output_has_injection(result.text):
        return TranslationAnswer(
            False,
            error="The AI response included instructions, so nothing was shown. Please try again.",
            log_id=result.log_id,
        )
    translated = sanitize_output(result.text, limit=MAX_INPUT)
    if not translated:
        return TranslationAnswer(
            False, error="The AI service returned no translation.", log_id=result.log_id
        )
    return TranslationAnswer(
        True, text=translated, source=source, target=target, log_id=result.log_id
    )


def _figure_lines(request):
    """Only locally calculated scalar figures, never a person or a record."""
    from academics.views import _role_dashboard_figures, _staff_dashboard_figures

    staff = _staff_dashboard_figures(request)
    role = _role_dashboard_figures(request)
    rows = [
        ("Active students", staff["students"], "count"),
        ("Students with an outstanding balance", staff["owing"], "count"),
        ("Courses", staff["courses"], "count"),
        ("Total billed", role["total_billed"], "money"),
        ("Total paid", role["total_paid"], "money"),
        ("Total waived", role["total_waived"], "money"),
        ("Outstanding", role["outstanding"], "money"),
    ]
    if role.get("collection_rate") is not None:
        rows.append(("Collection rate", role["collection_rate"], "percent"))
    if role.get("attendance_pct") is not None:
        rows.append(("Attendance", role["attendance_pct"], "percent"))
    figures = []
    for label, value, kind in rows:
        if kind == "money":
            shown = f"{Decimal(value):,.2f}"
        elif kind == "percent":
            shown = f"{float(value):.1f}%"
        else:
            shown = f"{int(value)}"
        figures.append({"label": label, "value": shown})
    return tuple(figures)


def _figure_numbers(text):
    return [group.replace(",", "") for group in
            re.findall(r"\d[\d,]*\.?\d*", text or "")]


def insights_for_user(request, *, provider=None):
    """Wrap already-calculated figures in plain language, or show them directly."""
    user = request.user
    if not user_has_any_code(user, ("reports.view_dashboards", "admin.dashboard_school")):
        return InsightAnswer(False, error="Your role cannot see these dashboard figures.")
    try:
        figures = _figure_lines(request)
    except Exception:
        return InsightAnswer(False, error="Dashboard figures are not available right now.")
    lines = [f"{item['label']}: {item['value']}" for item in figures]
    result = ask_ai_for_user(
        AIRequest(
            feature=Feature.INSIGHTS,
            system_prompt=(
                "Rewrite the supplied labelled figures in plain language. "
                "Keep every number exactly as supplied. Add no new facts, names "
                "or figures. Return at most 120 words."
            ),
            user_prompt="\n".join(lines),
            tier="fast",
            max_tokens=300,
        ),
        user,
        getattr(request, "institution", None),
        provider=provider,
        names=names_for(user, getattr(request, "institution", None)),
        http_request=request,
    )
    if not result.ok:
        return InsightAnswer(False, error=result.error, log_id=result.log_id)
    text = sanitize_output(result.text, limit=1200)
    expected = [number for item in figures for number in _figure_numbers(item["value"])]
    normalized = text.replace(",", "")
    if (not text or output_has_injection(result.text)
            or any(number not in normalized for number in expected)):
        text = "Here are the current figures:\n" + "\n".join(lines)
        return InsightAnswer(
            True, text=text, figures=figures, reviewed=False, log_id=result.log_id
        )
    return InsightAnswer(
        True, text=text, figures=figures, reviewed=True, log_id=result.log_id
    )


def natural_search(user, query, request=None, *, provider=None):
    """Map a request to fixed codes, then search with the normal permission rules."""
    if not user_has_code(user, "search.global"):
        return SearchAnswer(False, error="Your role cannot use school search.")
    query = str(query or "").strip()[:160]
    if len(query) < 2:
        return SearchAnswer(False, error="Type at least two characters to search.")
    options = "\n".join(f"{code} = {label}" for code, label in SEARCH_TYPES)
    result = ask_ai_for_user(
        AIRequest(
            feature=Feature.SEARCH,
            system_prompt=(
                "Choose zero or more record type codes from the fixed list. "
                "Reply with only the chosen codes separated by commas, or none. "
                "Never write queries, conditions, code or SQL."
            ),
            user_prompt=(
                f"Request: {query}\nAvailable codes (labels are for you only):\n{options}"
            ),
            tier="fast",
            max_tokens=80,
        ),
        user,
        getattr(request, "institution", None) if request is not None else None,
        provider=provider,
        names=names_for(user, getattr(request, "institution", None) if request is not None else None),
        http_request=request,
    )
    if not result.ok:
        return SearchAnswer(False, error=result.error, log_id=result.log_id)
    if output_has_injection(result.text):
        return SearchAnswer(
            False,
            error="Name a record type to search: student, staff, course, receipt or application.",
            log_id=result.log_id,
        )
    tokens = set(re.findall(r"[a-z]+", sanitize_output(result.text, limit=200).lower()))
    chosen = tuple(code for code in SEARCH_CODES if code in tokens)
    if not chosen:
        return SearchAnswer(
            False,
            error="Name a record type to search: student, staff, course, receipt or application.",
            log_id=result.log_id,
        )
    groups = search_groups(
        user, query,
        institution=getattr(request, "institution", None) if request is not None else None,
        codes=chosen,
    )
    return SearchAnswer(
        True, chosen=chosen, groups=tuple(groups), log_id=result.log_id
    )
