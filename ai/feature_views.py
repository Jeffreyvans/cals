"""Screens for the five optional AI assistants.

These screens only render results. They never write a business record or send a
message, so a person must copy an approved draft into the ordinary workflow.
"""

from django.http import Http404
from django.shortcuts import render

from core.access import user_has_code
from core.permissions import permission_required_code

from .assistance import (
    DRAFT_KINDS,
    DRAFT_LABEL,
    LANGUAGES,
    REVIEW_LABEL,
    help_answer,
    insights_for_user,
    make_draft,
    natural_search,
    translate_text,
)
from .features import Feature


def _institution(request):
    inst = getattr(request, "institution", None)
    return inst if inst is not None else getattr(request.user, "institution", None)


def _require_institution(request):
    inst = _institution(request)
    if inst is None:
        raise Http404("No institution is linked to this account.")
    return inst


@permission_required_code("ai.use")
def help_assistant(request):
    """Answer questions from the signed-in person's in-app guide only."""
    inst = _require_institution(request)
    answer = None
    if request.method == "POST":
        answer = help_answer(
            request.user, request.POST.get("question", ""),
            institution=inst, http_request=request,
        )
    return render(request, "ai/help.html", {
        "answer": answer, "feedback_feature": Feature.HELP,
    })


@permission_required_code("ai.use")
def draft_view(request):
    """Show an editable preview. There is deliberately no save or send action."""
    inst = _require_institution(request)
    kinds = [(code, label) for code, (label, _guidance) in DRAFT_KINDS.items()]
    answer = None
    kind = request.POST.get("kind", "report_card")
    if request.method == "POST":
        answer = make_draft(
            request.user, kind, request.POST.get("instructions", ""),
            institution=inst, http_request=request,
        )
    return render(request, "ai/draft.html", {
        "answer": answer, "kinds": kinds, "kind": kind, "draft_label": DRAFT_LABEL,
        "feedback_feature": Feature.DRAFT,
    })


@permission_required_code("ai.use")
def translation_view(request):
    """Translate short text and label it for human review."""
    inst = _require_institution(request)
    answer = None
    source = request.POST.get("source", "en")
    target = request.POST.get("target", "sn")
    if request.method == "POST":
        answer = translate_text(
            request.user, source, target, request.POST.get("text", ""),
            institution=inst, http_request=request,
        )
    return render(request, "ai/translation.html", {
        "answer": answer, "languages": LANGUAGES.items(),
        "source": source, "target": target, "review_label": REVIEW_LABEL,
        "feedback_feature": Feature.TRANSLATION,
    })


@permission_required_code("ai.use")
def insights_view(request):
    """Show locally calculated figures with an optional plain-language wrapper."""
    _require_institution(request)
    answer = None
    if request.method == "POST":
        answer = insights_for_user(request)
    return render(request, "ai/insights.html", {
        "answer": answer, "feedback_feature": Feature.INSIGHTS,
    })


@permission_required_code("ai.use")
def search_view(request):
    """Turn a request into fixed, permission-checked record type filters."""
    _require_institution(request)
    answer = None
    query = request.POST.get("q", "")
    if request.method == "POST":
        answer = natural_search(request.user, query, request=request)
    return render(request, "ai/search.html", {
        "answer": answer, "query": query,
        "can_search": user_has_code(request.user, "search.global"),
        "feedback_feature": Feature.SEARCH,
    })
