"""Short-window burst limits that complement the daily and monthly caps.

The usage log limits in :mod:`ai.usage` stop sustained overuse. These cache
buckets stop a person or a shared connection from making a rapid burst of
calls. An attempt is counted even when another control refuses it, so opening a
switch cannot be used to reset the burst window.
"""

from __future__ import annotations

from django.conf import settings
from django.core.cache import cache

RATE_MESSAGE = (
    "You are sending AI requests too quickly. Nothing was sent; please wait a "
    "moment and try again."
)


def _positive(name, default):
    try:
        return max(1, int(getattr(settings, name, default)))
    except (TypeError, ValueError):
        return default


def _client_ip(request):
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or None


def _targets(user, institution, request):
    targets = []
    user_id = getattr(user, "pk", None)
    if user_id:
        targets.append((
            f"edusphere.ai.rate.user.{user_id}",
            _positive("AI_RATE_LIMIT", 10),
        ))
    ip = _client_ip(request)
    if ip:
        school_id = getattr(institution, "pk", institution) or "none"
        targets.append((
            f"edusphere.ai.rate.ip.{school_id}.{ip}",
            _positive("AI_RATE_IP_LIMIT", 30),
        ))
    return targets


def rate_limit_error(user, institution=None, request=None):
    """Return a safe message when a per-user or per-IP bucket is full."""
    for key, limit in _targets(user, institution, request):
        try:
            used = int(cache.get(key, 0) or 0)
        except (TypeError, ValueError):
            used = 0
        if used >= limit:
            return RATE_MESSAGE
    return ""


def record_attempt(user, institution=None, request=None):
    """Count one attempt in every applicable short-window bucket."""
    window = _positive("AI_RATE_WINDOW_SECONDS", 60)
    for key, _limit in _targets(user, institution, request):
        try:
            cache.set(key, int(cache.get(key, 0) or 0) + 1, window)
        except Exception:
            # A cache outage must not stop the existing controls working.
            pass
