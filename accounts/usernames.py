"""Username generation, reservation and identifier helpers.

Usernames are lowercase and readable. Once a name has been used it is reserved
forever, even if the account that held it is later deleted, so no one else can
sign in under an old identity.
"""

from __future__ import annotations

import re
import unicodedata

from django.db import IntegrityError, transaction

MAX_USERNAME_LENGTH = 150


def normalize_identifier(value):
    return str(value or "").strip()


def normalize_email(value):
    return normalize_identifier(value).lower()


def _ascii_fold(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    return value.encode("ascii", "ignore").decode("ascii")


def _username_key(value):
    return normalize_identifier(value).lower()[:MAX_USERNAME_LENGTH]


def sanitize_username_base(value):
    value = _ascii_fold(value).lower()
    value = re.sub(r"[^a-z0-9._-]+", "", value)
    value = value.strip("._-")
    return value[:MAX_USERNAME_LENGTH]


def base_from_email(email):
    local = normalize_email(email).split("@", 1)[0]
    return sanitize_username_base(local)


def username_taken(username):
    """True when the name belongs to a user now or was ever reserved."""
    from accounts.models import ReservedUsername, User

    key = _username_key(username)
    if not key:
        return False
    return (
        User.objects.filter(username__iexact=key).exists()
        or ReservedUsername.objects.filter(username=key).exists()
    )


def reserve_username(username):
    """Record a username as used. Safe to call repeatedly."""
    from accounts.models import ReservedUsername

    key = _username_key(username)
    if not key:
        return None
    try:
        with transaction.atomic():
            obj, _created = ReservedUsername.objects.get_or_create(username=key)
            return obj
    except IntegrityError:
        return ReservedUsername.objects.filter(username=key).first()


def generate_unique_username(email="", base="", fallback="user"):
    """Return a free, readable, lowercase username and reserve it."""
    candidate_base = sanitize_username_base(base) or base_from_email(email)
    candidate_base = candidate_base or sanitize_username_base(fallback) or "user"
    candidate_base = candidate_base[:30] or "user"
    suffix = 0
    while True:
        if suffix == 0:
            candidate = candidate_base[:30]
        else:
            tail = str(suffix)
            candidate = f"{candidate_base[: max(1, 30 - len(tail))]}{tail}"
        candidate = _username_key(candidate)
        if candidate and not username_taken(candidate):
            reserve_username(candidate)
            return candidate
        suffix += 1


def ensure_username_available(username, exclude_user=None):
    """Raise ValidationError if the name is taken or reserved."""
    from django.core.exceptions import ValidationError

    from accounts.models import ReservedUsername, User

    key = _username_key(username)
    if not key:
        raise ValidationError("Choose a username.")
    users = User.objects.filter(username__iexact=key)
    if exclude_user is not None:
        users = users.exclude(pk=exclude_user.pk)
    if users.exists() or ReservedUsername.objects.filter(username=key).exists():
        raise ValidationError("That username is already in use. Choose another one.")
    return key
