"""Dynamic host and CSRF origin registration for verified custom domains.

Django validates ``Host`` before a view runs. A verified custom domain therefore
has to be present in ``settings.ALLOWED_HOSTS`` and, for POSTs, in
``settings.CSRF_TRUSTED_ORIGINS``. The deployment environment remains the source
of the static host list; this module adds only domains that are verified,
enabled and attached to an active institution, and removes the ones it added
when they are disabled or removed.

The database is read through a short-lived cache. Writes to an institution,
module, plan or subscription invalidate it immediately, so a domain change is
visible on the very next request.
"""

from __future__ import annotations

import threading

from django.conf import settings
from django.core.cache import cache

from core.cache import _key, get_or_set

HOSTS_CACHE_KEY = "edusphere:custom-hosts"
HOSTS_CACHE_TTL = 300

_lock = threading.Lock()
_applied_hosts: set[str] = set()
_applied_origins: set[str] = set()


def verified_domains():
    """Canonical host names that are allowed to resolve an institution."""
    def build():
        from core.models import Institution
        from enterprise.access import feature_enabled

        rows = Institution.objects.filter(
            custom_domain__isnull=False,
            custom_domain_enabled=True,
            custom_domain_verified=True,
            is_active=True,
        ).exclude(custom_domain="")
        domains = set()
        for institution in rows.only("pk", "custom_domain", "type"):
            if feature_enabled("white_label", institution):
                domain = str(institution.custom_domain or "").strip().lower().rstrip(".")
                if domain:
                    domains.add(domain)
        return sorted(domains)

    return get_or_set(HOSTS_CACHE_KEY, build, HOSTS_CACHE_TTL)


def _origins_for(hosts):
    origins = {f"https://{host}" for host in hosts}
    if getattr(settings, "DEBUG", False):
        origins.update(f"http://{host}" for host in hosts)
    return sorted(origins)


def refresh_dynamic_hosts():
    """Reconcile verified domains with Django's runtime host settings."""
    global _applied_hosts, _applied_origins

    hosts = verified_domains()
    origins = _origins_for(hosts)
    with _lock:
        current_hosts = [str(value) for value in getattr(settings, "ALLOWED_HOSTS", []) or []]
        current_origins = [
            str(value) for value in getattr(settings, "CSRF_TRUSTED_ORIGINS", []) or []
        ]
        # Never remove a host that was configured by the deployment itself.
        base_hosts = [host for host in current_hosts if host not in _applied_hosts]
        base_origins = [
            origin for origin in current_origins if origin not in _applied_origins
        ]
        new_hosts = [host for host in hosts if host not in base_hosts]
        new_origins = [origin for origin in origins if origin not in base_origins]
        settings.ALLOWED_HOSTS = base_hosts + new_hosts
        settings.CSRF_TRUSTED_ORIGINS = base_origins + new_origins
        _applied_hosts = set(new_hosts)
        _applied_origins = set(new_origins)
    return list(settings.ALLOWED_HOSTS)


def invalidate_dynamic_hosts():
    """Drop the cached host list and apply the current database state now."""
    cache.delete(_key("custom-hosts"))
    cache.delete(HOSTS_CACHE_KEY)
    return refresh_dynamic_hosts()
