"""Small DNS helpers used by the manual custom-domain workflow."""

from __future__ import annotations

import socket


def domain_resolves(hostname, timeout=3):
    """True when the hostname has at least one A/AAAA/CNAME answer.

    This is a reachability check, not a proof that the TXT token is published.
    Platform staff still confirm the token and TLS setup explicitly in the
    domain screen; see docs/WHITE_LABEL.md.
    """
    if not hostname:
        return False
    try:
        socket.setdefaulttimeout(timeout)
        return bool(socket.getaddrinfo(hostname, None))
    except (OSError, ValueError):
        return False
    finally:
        socket.setdefaulttimeout(None)
