"""Usage counts, limits, cost estimates and the privacy-preserving log."""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from .features import FEATURE_LABELS
from .models import AIUsageLog


@dataclass(frozen=True)
class UsageCounts:
    institution_day: int
    institution_month: int
    user_day: int
    user_month: int

    def as_dict(self):
        return {
            "institution_day": self.institution_day,
            "institution_month": self.institution_month,
            "user_day": self.user_day,
            "user_month": self.user_month,
        }


def period_starts(now=None):
    now = now or timezone.now()
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return day, day.replace(day=1)


def usage_counts(institution, user=None, now=None):
    """Count attempts in the current day and month, for school and person."""
    day, month = period_starts(now)
    rows = AIUsageLog.all_objects.filter(institution=institution)
    user_rows = rows.filter(user=user) if user is not None else rows.none()
    return UsageCounts(
        institution_day=rows.filter(created__gte=day).count(),
        institution_month=rows.filter(created__gte=month).count(),
        user_day=user_rows.filter(created__gte=day).count(),
        user_month=user_rows.filter(created__gte=month).count(),
    )


def limit_error(setting, counts):
    """Return a user-facing message when any of the four limits is reached."""
    if setting.institution_daily_limit <= counts.institution_day:
        return (
            "Your institution has reached today's AI limit. "
            "Nothing was sent; please try again tomorrow."
        )
    if setting.institution_monthly_limit <= counts.institution_month:
        return (
            "Your institution has reached this month's AI limit. "
            "Nothing was sent; please try again next month."
        )
    if setting.user_daily_limit <= counts.user_day:
        return (
            "You have reached your daily AI limit. "
            "Nothing was sent; please try again tomorrow."
        )
    if setting.user_monthly_limit <= counts.user_month:
        return (
            "You have reached your monthly AI limit. "
            "Nothing was sent; please try again next month."
        )
    return ""


def _rate(name):
    raw = os.environ.get(name, "0")
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return Decimal("0")
    return value if value >= 0 else Decimal("0")


def estimate_cost(input_tokens=0, output_tokens=0):
    """A rough estimate from operator-supplied per-million-token rates."""
    million = Decimal(1_000_000)
    cost = (
        Decimal(input_tokens) * _rate("AI_COST_PER_MILLION_INPUT")
        + Decimal(output_tokens) * _rate("AI_COST_PER_MILLION_OUTPUT")
    ) / million
    return cost.quantize(Decimal("0.000001"))


def record_usage(
    user,
    institution,
    feature,
    *,
    status=AIUsageLog.Status.SUCCESS,
    model_name="",
    tier="fast",
    input_tokens=0,
    output_tokens=0,
    prompt_summary="",
):
    """Append one log row with no full prompt, answer or personal content."""
    return AIUsageLog.all_objects.create(
        institution=institution,
        user=user if getattr(user, "is_authenticated", False) else None,
        feature=feature,
        status=status,
        model_name=str(model_name)[:120],
        tier=tier,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_estimate=estimate_cost(input_tokens, output_tokens),
        prompt_summary=str(prompt_summary)[:180],
    )


def feature_rows(institution):
    """Label/setting pairs for the usage screen, including untouched features."""
    from .features import Feature
    from .models import AIFeatureSetting

    settings = {
        row.feature: row
        for row in AIFeatureSetting.all_objects.filter(institution=institution)
    }
    rows = []
    for code, label in Feature.choices:
        rows.append(
            {
                "code": code,
                "label": FEATURE_LABELS.get(code, label),
                "setting": settings.get(code),
            }
        )
    return rows
