"""Two-factor and fresh-password checks for platform staff.

No extra package is needed: the time-based one-time password is the standard
RFC 6238 construction, which any authenticator app can read.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from datetime import timedelta
from urllib.parse import quote

from django.utils import timezone

REAUTH_MINUTES = 15
STEP_SECONDS = 30
DIGITS = 6


def new_secret():
    """A 20-byte base32 secret, the length RFC 4226 recommends."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _normalise(secret):
    padded = secret.upper().strip()
    return padded + "=" * ((8 - len(padded) % 8) % 8)


def code_at(secret, timestamp=None, step=0):
    """The six-digit code for a counter step."""
    if timestamp is None:
        timestamp = timezone.now().timestamp()
    counter = int(timestamp // STEP_SECONDS) + step
    key = base64.b32decode(_normalise(secret))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{number % (10 ** DIGITS):0{DIGITS}d}"


def verify(secret, code):
    """Accept the current code or one step either side of it."""
    if not secret or not code:
        return False
    code = str(code).strip().replace(" ", "")
    return any(
        hmac.compare_digest(code_at(secret, step=step), code)
        for step in (-1, 0, 1)
    )


def provisioning_uri(secret, email, issuer="EduSphere"):
    label = quote(f"{issuer}:{email or 'platform'}")
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
        f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"
    )


def reauth_session_key(user):
    return f"platform_reauth_{user.pk}"


def mark_reauth(request, staff):
    """Record a password re-check in both the session and the staff row."""
    now = timezone.now()
    staff.last_reauth_at = now
    staff.save(update_fields=["last_reauth_at"])
    if request is not None and hasattr(request, "session"):
        request.session[reauth_session_key(request.user)] = now.isoformat()


def has_recent_reauth(request, staff, minutes=REAUTH_MINUTES):
    if staff is None:
        return False
    now = timezone.now()
    if request is not None and hasattr(request, "session"):
        raw = request.session.get(reauth_session_key(request.user))
        if raw:
            try:
                from datetime import datetime

                stamp = datetime.fromisoformat(raw)
                if timezone.is_naive(stamp):
                    stamp = timezone.make_aware(stamp)
                if now - stamp <= timedelta(minutes=minutes):
                    return True
            except (TypeError, ValueError):
                pass
    last = staff.last_reauth_at
    return last is not None and now - last <= timedelta(minutes=minutes)


def ready_for_sensitive_action(staff, request=None):
    """Return an empty string when allowed, or a message explaining the block."""
    if staff is None or not staff.is_active:
        return "Only active platform staff may do this."
    if not staff.two_factor_enabled or not staff.two_factor_secret:
        return "Switch on two-factor authentication before asking for institution data."
    if not has_recent_reauth(request, staff):
        return "Enter your password again before asking for institution data."
    return ""
