"""Shareable public sign-up links.

An administrator creates a link and hands it out (by email, a poster or a
WhatsApp message). Anyone who opens it can register at that one institution,
even when public registration is switched off, because the link itself is the
invitation. The link can be limited to students or staff, preset to a staff
position, and can expire or run out of uses.

The heavy lifting is still done by ``register_student`` and ``register_staff``;
this module only decides who may use a link and records each use.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError

from core.audit import log_action

from .models import SignupLink, SignupLinkUse
from .registration import (
    FORBIDDEN_POSITIONS,
    STAFF_POSITIONS,
    allowed_staff_positions,
    normalize_text,
)

# A link is refused after this many attempts from one address.
LINK_RATE_LIMIT = 10
LINK_RATE_WINDOW = 15 * 60


def _validate_kind(kind):
    if kind not in SignupLink.Kind.values:
        raise ValidationError("Choose whether the link is for students, staff or both.")


def _validate_position(kind, position):
    position = normalize_text(position)
    if not position:
        return ""
    if position in FORBIDDEN_POSITIONS or position not in STAFF_POSITIONS:
        raise ValidationError("Choose a staff position from the list.")
    if kind == SignupLink.Kind.STUDENT:
        raise ValidationError("A student link cannot carry a staff position.")
    return position


def create_signup_link(
    institution,
    *,
    label="",
    kind=SignupLink.Kind.ANY,
    position="",
    expires_at=None,
    max_uses=None,
    actor=None,
    request=None,
):
    """Create a link for one institution. Returns the saved ``SignupLink``."""
    _validate_kind(kind)
    position = _validate_position(kind, position)
    if max_uses is not None:
        try:
            max_uses = int(max_uses)
        except (TypeError, ValueError) as exc:
            raise ValidationError("The maximum number of uses must be a whole number.") from exc
        if max_uses < 1:
            raise ValidationError("The maximum number of uses must be at least one.")
    link = SignupLink.objects.create(
        institution=institution,
        label=normalize_text(label)[:120],
        kind=kind,
        position=position,
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
        expires_at=expires_at,
        max_uses=max_uses,
    )
    log_action(
        actor,
        "signup_link.created",
        instance=link,
        institution=institution,
        after={"kind": kind, "position": position, "max_uses": max_uses},
        request=request,
    )
    return link


def revoke_signup_link(link, actor=None, request=None):
    """Stop a link working without deleting its history."""
    if not link.is_active:
        return link
    link.is_active = False
    link.save(update_fields=["is_active"])
    log_action(
        actor, "signup_link.revoked", instance=link,
        institution=link.institution, request=request,
    )
    return link


def reactivate_signup_link(link, actor=None, request=None):
    """Turn a link back on."""
    if link.is_active:
        return link
    link.is_active = True
    link.save(update_fields=["is_active"])
    log_action(
        actor, "signup_link.reactivated", instance=link,
        institution=link.institution, request=request,
    )
    return link


def delete_signup_link(link, actor=None, request=None):
    """Remove a link for good, keeping one audit row behind."""
    institution = link.institution
    log_action(
        actor, "signup_link.deleted", instance=link,
        institution=institution,
        after={"label": link.label, "kind": link.kind},
        request=request,
    )
    link.delete()


def link_is_open(link):
    """True when a visitor may still use this link."""
    return link.is_open


def link_positions(link):
    """The staff positions a link offers.

    A preset position wins. Otherwise the institution's own public list is
    used, falling back to every position a public form may offer when the
    institution has switched none on.
    """
    if link.position:
        return [link.position]
    allowed = allowed_staff_positions(link.institution)
    if allowed:
        return allowed
    return [code for code in STAFF_POSITIONS if code not in FORBIDDEN_POSITIONS]


def link_offers(link):
    """Which paths the link opens: a set of ``student`` and/or ``staff``."""
    if link.kind == SignupLink.Kind.STUDENT:
        return {"student"}
    if link.kind == SignupLink.Kind.STAFF:
        return {"staff"}
    return {"student", "staff"}


def open_signup_links(institution):
    """The institution's links that can still be used, newest first."""
    if institution is None:
        return []
    return [
        link
        for link in institution.signup_links.filter(is_active=True)
        if link.is_open
    ]


def record_use(link, kind, user=None, email="", request=None):
    """Remember that a registration came through this link."""
    use = SignupLinkUse.objects.create(
        link=link,
        user=user if getattr(user, "pk", None) else None,
        email=normalize_text(email).lower()[:200],
        kind=kind,
    )
    log_action(
        user if getattr(user, "pk", None) else None,
        "signup_link.used",
        instance=link,
        institution=link.institution,
        after={"kind": kind, "email": use.email},
        request=request,
    )
    return use


def link_rate_limited(request, link, limit=LINK_RATE_LIMIT, window=LINK_RATE_WINDOW):
    from django.core.cache import cache

    from .registration import _client_ip

    key = f"edusphere.signup.{link.pk}.{_client_ip(request)}"
    return cache.get(key, 0) >= limit


def record_link_attempt(request, link, window=LINK_RATE_WINDOW):
    from django.core.cache import cache

    from .registration import _client_ip

    key = f"edusphere.signup.{link.pk}.{_client_ip(request)}"
    cache.set(key, cache.get(key, 0) + 1, window)


__all__ = [
    "LINK_RATE_LIMIT",
    "LINK_RATE_WINDOW",
    "create_signup_link",
    "delete_signup_link",
    "link_is_open",
    "link_offers",
    "link_positions",
    "link_rate_limited",
    "open_signup_links",
    "reactivate_signup_link",
    "record_link_attempt",
    "record_use",
    "revoke_signup_link",
]
