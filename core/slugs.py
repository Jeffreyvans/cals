"""Safe, reusable institution web addresses (slugs).

An institution slug is a public address: it appears in ``/i/<slug>/``, in the
branded login link and in a subdomain. Three rules matter:

* it must be a plain lower-case slug (no spaces, dots or reserved words);
* it must not collide with another institution or with a top-level route;
* when an institution changes its slug, the old one keeps working for 90 days
  so printed posters and shared links do not suddenly break.

This module is the single place those rules live. Forms, the setup wizard and
the platform services all call it instead of repeating their own checks.
"""

from __future__ import annotations

import re
import secrets
from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.text import slugify

MAX_SLUG_LENGTH = 100
SLUG_REDIRECT_DAYS = 90

# Top-level routes and words that would make a confusing or dangerous
# institution address. ``www`` and ``app`` are kept clear for future hosts.
RESERVED_SLUGS = frozenset({
    "admin", "api", "apply", "app", "dashboard", "default", "finance",
    "fiscalisation", "health", "help", "home", "i", "ict", "imports", "jobs",
    "join", "learn", "library", "login", "logout", "media", "office",
    "offline", "platform", "privacy", "register", "reset", "robots", "root",
    "services", "settings", "setup", "sitemap", "static", "support", "sw",
    "users", "webhooks", "www",
})

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)


def normalize_slug(value):
    """Turn a name into a slug, or return an empty string."""
    return slugify(value or "")[:MAX_SLUG_LENGTH]


def validate_slug(value, *, exclude=None, normalize=True):
    """Return a safe unused slug, or raise :class:`ValidationError`.

    ``exclude`` is an institution (or pk) whose own slug may be kept. Pass
    ``normalize=False`` when the caller has already checked that the user's
    text is itself a valid slug (the setup wizard does this to keep its
    long-standing "not a slug" error message).
    """
    candidate = normalize_slug(value) if normalize else (value or "").strip()
    candidate = candidate[:MAX_SLUG_LENGTH]
    if not candidate:
        raise ValidationError("Enter a name that can become a web address.")
    if not _SLUG_RE.fullmatch(candidate):
        raise ValidationError("The web address may only contain small letters, numbers and dashes.")
    if candidate in RESERVED_SLUGS:
        raise ValidationError(
            f"'{candidate}' is reserved for the platform. Choose another web address."
        )

    # Imported here so this module can be used from model definitions without
    # creating an import cycle.
    from core.models import Institution, InstitutionSlugRedirect

    institution_qs = Institution.objects.filter(slug=candidate)
    exclude_pk = getattr(exclude, "pk", exclude)
    if exclude_pk is not None:
        institution_qs = institution_qs.exclude(pk=exclude_pk)
    if institution_qs.exists():
        raise ValidationError(f"The web name '{candidate}' is already taken.")

    redirect_qs = InstitutionSlugRedirect.objects.filter(
        old_slug=candidate, expires_at__gt=timezone.now()
    )
    if exclude_pk is not None:
        redirect_qs = redirect_qs.exclude(institution_id=exclude_pk)
    if redirect_qs.exists():
        raise ValidationError(
            "That web address was used recently and is still redirecting. "
            "Choose another one for now."
        )
    return candidate


def unique_slug(name, *, exclude=None):
    """Build a free slug from a name, adding ``-2``, ``-3`` if necessary."""
    base = normalize_slug(name) or "institution"
    candidate = base
    counter = 1
    while True:
        try:
            return validate_slug(candidate, exclude=exclude)
        except ValidationError:
            counter += 1
            suffix = f"-{counter}"
            candidate = f"{base[:MAX_SLUG_LENGTH - len(suffix)]}{suffix}"


def normalize_domain(value):
    """Validate a custom hostname and return it in canonical lower case."""
    value = (value or "").strip().lower().rstrip(".")
    if value.startswith(("http://", "https://")):
        value = (urlparse(value).hostname or "").lower()
    if not value:
        raise ValidationError("Enter the domain name you want to use.")
    if not _DOMAIN_RE.fullmatch(value):
        raise ValidationError(
            "Enter a full domain such as learn.example.org, without https://."
        )
    return value


def domain_verification_token():
    """The value the institution publishes in its DNS TXT record."""
    return secrets.token_hex(24)
