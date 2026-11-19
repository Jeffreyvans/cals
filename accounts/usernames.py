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
USERNAME_HELP_TEXT = "Leave blank and we will create one from the email address."
USERNAME_FORMAT_HINT = "Use 4 to 30 letters, numbers, dots, hyphens or underscores."

# Names that must never be handed to a person. They look like a role or a
# system account and would make sign-in and support conversations ambiguous.
RESERVED_USERNAMES = frozenset(
    {
        "abuse", "admin", "administrator", "api", "billing", "contact", "guest",
        "help", "helpdesk", "hostmaster", "info", "it", "mail", "noreply",
        "no-reply", "null", "ops", "postmaster", "public", "root", "sales",
        "security", "staff", "support", "sys", "system", "superuser", "test",
        "undefined", "webmaster", "www",
    }
)


def is_reserved_username(value):
    return _username_key(value) in RESERVED_USERNAMES


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


def username_taken(username, exclude_user=None):
    """True when the name belongs to a user now or was ever reserved."""
    from accounts.models import ReservedUsername, User

    key = _username_key(username)
    if not key:
        return False
    users = User.objects.filter(username__iexact=key)
    if exclude_user is not None:
        users = users.exclude(pk=exclude_user.pk)
    owns_name = exclude_user is not None and _username_key(exclude_user.username) == key
    reserved = False if owns_name else ReservedUsername.objects.filter(username=key).exists()
    return users.exists() or reserved


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


def claim_username(username):
    """Reserve a free username for a new account and return its key.

    Returns None when the name is already taken or was ever used before.
    """
    from accounts.models import ReservedUsername, User

    key = _username_key(username)
    if not key or is_reserved_username(key):
        return None
    if User.objects.filter(username__iexact=key).exists():
        return None
    try:
        with transaction.atomic():
            obj, created = ReservedUsername.objects.get_or_create(username=key)
    except IntegrityError:
        return None
    return key if created else None


def generate_unique_username(email="", base="", fallback="user"):
    """Return a free, readable, lowercase username and reserve it."""
    candidate_base = sanitize_username_base(base) or base_from_email(email)
    candidate_base = candidate_base or sanitize_username_base(fallback) or "user"
    candidate_base = candidate_base[:30] or "user"
    # A generated login must satisfy the same 4-character minimum as a chosen
    # one, even when someone has an email address such as a@example.org.
    if len(candidate_base) < 4:
        candidate_base = f"{candidate_base}user"[:30]
    suffix = 0
    while True:
        if suffix == 0:
            candidate = candidate_base[:30]
        else:
            tail = str(suffix)
            candidate = f"{candidate_base[: max(1, 30 - len(tail))]}{tail}"
        candidate = _username_key(candidate)
        if not candidate:
            suffix += 1
            continue
        claimed = claim_username(candidate)
        if claimed:
            return claimed
        suffix += 1


def ensure_username_available(username, exclude_user=None):
    """Return the lowercase key when it is free and has never been used.

    This is the low-level check shared with older registration and import
    flows. Person-facing forms use :func:`validate_username`, which also
    enforces the length/format rules and reserved-name messages.
    """
    from django.core.exceptions import ValidationError

    from accounts.models import ReservedUsername, User

    key = _username_key(username)
    if not key:
        raise ValidationError("Choose a username.")
    if is_reserved_username(key):
        raise ValidationError("That username is reserved. Choose another one.")
    users = User.objects.filter(username__iexact=key)
    if exclude_user is not None:
        users = users.exclude(pk=exclude_user.pk)
    owns_name = exclude_user is not None and _username_key(exclude_user.username) == key
    reserved = (
        False if owns_name
        else ReservedUsername.objects.filter(username=key).exists()
    )
    if users.exists() or reserved:
        raise ValidationError("That username is already in use. Choose another one.")
    return key


def validate_username(value, exclude_user=None):
    """Validate a person-chosen username and return its lowercase key.

    This is the single source of truth for the rules shown on every add-user
    and username-edit form: 4 to 30 characters, no spaces, only letters,
    digits, dots, hyphens and underscores, starting with a letter or digit,
    unique platform-wide (case-insensitively) and never a reserved name.
    """
    from django.core.exceptions import ValidationError

    raw = normalize_identifier(value)
    if not raw:
        raise ValidationError("Enter a username.")
    if len(raw) < 4 or len(raw) > 30:
        raise ValidationError("Use 4 to 30 characters.")
    if " " in raw:
        raise ValidationError("A username cannot contain spaces.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", raw):
        if not raw[0].isalnum():
            raise ValidationError("Start with a letter or a number.")
        raise ValidationError("Use only letters, numbers, dots, hyphens and underscores.")
    key = raw.lower()
    if is_reserved_username(key):
        raise ValidationError("That username is reserved. Choose another one.")

    from accounts.models import ReservedUsername, User

    users = User.objects.filter(username__iexact=key)
    if exclude_user is not None:
        users = users.exclude(pk=exclude_user.pk)
    owns_name = exclude_user is not None and _username_key(exclude_user.username) == key
    reserved = (
        False if owns_name
        else ReservedUsername.objects.filter(username=key).exists()
    )
    if users.exists() or reserved:
        raise ValidationError("That username is already in use. Choose another one.")
    return key


def generate_username_for_email(email="", full_name=""):
    """Make a readable, lowercase, free username from an email's local part."""
    seed = base_from_email(email)
    if not seed:
        first_name = normalize_identifier(full_name).split(None, 1)
        seed = sanitize_username_base(first_name[0] if first_name else "") or "user"
    return generate_unique_username(email=email, base=seed, fallback="user")
