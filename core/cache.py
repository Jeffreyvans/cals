"""Small, explicit cache helpers.

The cache is an optimisation, never a source of truth. Every value here can be
thrown away at any time and the next request rebuilds it from the database.
Writes that change an institution's settings, modules, terminology, branding or
money clear the keys they affect (see :mod:`core.cache_signals`), so a screen
never shows stale data after somebody presses Save.

Redis is used when ``REDIS_URL`` is configured; otherwise the process-local
memory cache still removes the repeated queries that make every page slower on
a small deployment.
"""

from __future__ import annotations

from django.conf import settings
from django.core.cache import cache

PREFIX = "edusphere"


def _key(*parts):
    return ":".join([PREFIX, *(str(part) for part in parts)])


def institution_key(institution_or_id, suffix):
    pk = getattr(institution_or_id, "pk", institution_or_id)
    return _key("institution", pk, suffix)


def get_or_set(key, builder, timeout=None):
    """Return a cached value, building it only when the key is missing."""
    value = cache.get(key)
    if value is not None:
        return value
    value = builder()
    if value is not None:
        cache.set(key, value, timeout)
    return value


# --- institution settings, branding, modules and terminology ---------------


def branding_for(institution):
    if institution is None:
        return None
    from core.branding import brand_variables

    return get_or_set(
        institution_key(institution, "branding"),
        lambda: brand_variables(institution),
        getattr(settings, "CACHE_TTL_BRANDING", 900),
    )


def modules_for(institution):
    if institution is None:
        return set()
    from core.modules import enabled_modules

    codes = get_or_set(
        institution_key(institution, "modules"),
        lambda: sorted(enabled_modules(institution)),
        getattr(settings, "CACHE_TTL_BRANDING", 900),
    )
    return set(codes or [])


def terminology_for(institution):
    if institution is None:
        from core.terminology import DEFAULTS

        return dict(DEFAULTS["college"])
    from core.terminology import terminology_for as build

    return get_or_set(
        institution_key(institution, "terminology"),
        lambda: build(institution),
        getattr(settings, "CACHE_TTL_BRANDING", 900),
    )


def school_settings_for(institution_id):
    """The mode (school vs college) of an institution, cached for a while."""
    if institution_id is None:
        return None

    def build():
        from academics.models import SchoolSettings

        row = SchoolSettings.all_objects.filter(
            institution_id=institution_id
        ).only("mode").first()
        return row.mode if row is not None else None

    return get_or_set(
        _key("institution", institution_id, "mode"),
        build,
        getattr(settings, "CACHE_TTL_BRANDING", 900),
    )


def invalidate_institution(institution_or_id):
    """Drop every cached value that belongs to one institution."""
    pk = getattr(institution_or_id, "pk", institution_or_id)
    if pk is None:
        return
    keys = [
        institution_key(pk, "branding"),
        institution_key(pk, "modules"),
        institution_key(pk, "terminology"),
        _key("institution", pk, "mode"),
        _key("institution", pk, "dashboard"),
        _key("institution", pk, "public"),
    ]
    slug = getattr(institution_or_id, "slug", None)
    if slug:
        keys.append(_key("institution", "slug", slug))
    cache.delete_many(keys)


def institution_by_slug(slug):
    """The active institution for a subdomain/path prefix, cached briefly."""
    if not slug:
        return None
    key = _key("institution", "slug", slug)

    def build():
        from core.models import Institution

        return Institution.objects.filter(slug=slug, is_active=True).first()

    return get_or_set(key, build, 300)


def invalidate_dashboard(institution_or_id):
    pk = getattr(institution_or_id, "pk", institution_or_id)
    if pk is not None:
        cache.delete_many([
            _key("institution", pk, "dashboard"),
            _key("institution", pk, "dashboard:staff"),
            _key("institution", pk, "dashboard:role"),
        ])


def dashboard_figures(institution_id, builder, *, kind="staff"):
    """Cache the numbers on a dashboard for a short time.

    The figures are invalidated whenever an invoice or payment changes. While
    the test suite runs the cache is bypassed so a test always sees the rows it
    has just written, even when it uses ``queryset.update()``.
    """
    if institution_id is None:
        return builder()
    if getattr(settings, "TESTING", False):
        return builder()
    key = _key("institution", institution_id, f"dashboard:{kind}")
    return get_or_set(
        key, builder, getattr(settings, "CACHE_TTL_DASHBOARD", 120)
    )


def invalidate_public(institution_or_id=None):
    if institution_or_id is None:
        cache.delete(_key("public", "learn"))
        return
    pk = getattr(institution_or_id, "pk", institution_or_id)
    cache.delete(institution_key(pk, "public"))


# --- permissions -----------------------------------------------------------


def permission_cache_key(user):
    """A key that changes when the account or its roles change.

    ``date_joined`` distinguishes two accounts that happen to reuse the same
    primary key (for example after a database restore or in a test database).
    """
    stamp = getattr(user, "date_joined", None)
    stamp = stamp.isoformat() if stamp else ""
    return _key(
        "perms",
        "v" + str(permission_version()),
        user.pk,
        getattr(user, "institution_id", "") or "",
        getattr(user, "role", "") or "",
        getattr(user, "is_superuser", False),
        stamp,
    )


def permission_version():
    """A counter bumped whenever a role or its permissions change."""
    key = _key("perms", "version")
    value = cache.get(key)
    if value is None:
        cache.set(key, 1, None)
        return 1
    return value


def bump_permission_version():
    """Invalidate every cached permission set in one cheap operation."""
    key = _key("perms", "version")
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, None)


def invalidate_permissions(user_or_id):
    """Best effort: the key includes the account stamp, so old keys expire."""
    pk = getattr(user_or_id, "pk", user_or_id)
    if pk is None:
        return
    # The full key needs the user row; a wildcard delete is not portable across
    # cache backends. A short TTL plus the stamp in the key is enough, but when
    # we have the object we can remove its exact key too.
    if hasattr(user_or_id, "pk"):
        cache.delete(permission_cache_key(user_or_id))
