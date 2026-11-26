"""Bind a freshly established session to the host that created it.

The binding is written during login, when Django already saves the session, so
ordinary requests do not pay for an extra session update. Sessions created
before this feature was installed are still bound lazily by
``HostSessionIsolationMiddleware`` on their next authenticated request.
"""

from __future__ import annotations


def bind_session_host(sender, request, user, **kwargs):
    session = getattr(request, "session", None)
    if session is None:
        return
    from core.middleware import HostSessionIsolationMiddleware

    host = HostSessionIsolationMiddleware._host(request)
    session[HostSessionIsolationMiddleware.SESSION_KEY] = host
