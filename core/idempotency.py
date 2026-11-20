"""Small cache-backed retry keys for payments and registrations.

A retry after a timeout must never create a second payment or a second account.
Forms carry a hidden ``idempotency_key`` (or send an ``Idempotency-Key`` header)
and the same value is reused by the browser's Retry button. Only a salted hash
is stored in the cache: the raw key is never logged.
"""

import hashlib
import hmac
import uuid

from django.conf import settings
from django.core.cache import cache

_HEADER = "HTTP_IDEMPOTENCY_KEY"
_FIELD = "idempotency_key"
_PREFIX = "edusphere:idempotency:v1:"


def new_key():
    """A fresh key to place in a hidden form field."""
    return uuid.uuid4().hex


def _raw_key(request, supplied=None):
    if supplied:
        return str(supplied)
    if request is None:
        return ""
    header = request.META.get(_HEADER, "")
    if header:
        return str(header)
    post = getattr(request, "POST", None)
    if post is not None:
        return str(post.get(_FIELD, "") or "")
    return ""


def token_for(request, scope, supplied=None):
    """A stable, non-reversible cache token for one scope and one retry key."""
    raw = _raw_key(request, supplied) or new_key()
    user = getattr(request, "user", None)
    actor = str(getattr(user, "pk", "") or "") if getattr(user, "is_authenticated", False) else ""
    salt = str(getattr(settings, "SECRET_KEY", "")).encode()
    digest = hmac.new(salt, f"{scope}:{actor}:{raw}".encode(), hashlib.sha256).hexdigest()
    return digest


def begin(request, scope, supplied=None):
    """Return ``(token, previous_result)`` for this retry key.

    ``previous_result`` is ``None`` the first time. A retried request can reuse
    the stored result instead of repeating the side effect.
    """
    token = token_for(request, scope, supplied)
    return token, cache.get(_PREFIX + token)


def finish(token, result, ttl=1800):
    """Remember the successful result so a retry is harmless."""
    cache.set(_PREFIX + token, result, ttl)
    return result


def forget(token):
    cache.delete(_PREFIX + token)
