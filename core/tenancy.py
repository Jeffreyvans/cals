"""Current-institution handling.

Every tenant table carries an ``institution`` foreign key. The current
institution is resolved in this order:

1. the value set for this request (middleware, tests, background jobs),
2. the default institution (the single school a fresh install starts with).

Reads through :class:`core.models.TenantManager` are filtered by the current
institution, so one institution can never see another institution's rows.
"""

from contextvars import ContextVar

from django.db import connections, router

_current_institution = ContextVar("edusphere_current_institution", default=None)
_default_ids = {}


def _db_alias():
    from core.models import Institution

    try:
        return router.db_for_read(Institution)
    except Exception:  # pragma: no cover - during very early startup
        return "default"


def _cache_key(alias):
    """Alias plus database name, so a test database never reuses another pk."""
    try:
        name = connections[alias].settings_dict.get("NAME")
    except Exception:  # pragma: no cover - alias not configured
        name = None
    return (alias, str(name))


def default_institution_id():
    """Return the pk of the default institution, or None before it exists.

    The default institution is created by a data migration, so a fresh
    database has it before any request is served.
    """
    from core.models import Institution

    alias = _db_alias()
    key = _cache_key(alias)
    cached = _default_ids.get(key)
    if cached is not None:
        return cached
    try:
        inst = Institution.objects.using(alias).filter(slug="default").first()
    except Exception:
        # The table does not exist yet (migrations, first boot).
        return None
    if inst is None:
        return None
    _default_ids[key] = inst.pk
    return inst.pk


def current_institution_id():
    """The institution whose rows the current code may read and write."""
    value = _current_institution.get()
    if value is not None:
        return value
    return default_institution_id()


def set_current_institution(institution):
    """Set the current institution for this thread. Returns a reset token."""
    if institution is None:
        value = None
    elif isinstance(institution, int):
        value = institution
    else:
        value = institution.pk
    return _current_institution.set(value)


def reset_current_institution(token):
    _current_institution.reset(token)


def use_institution(institution):
    """Context manager that makes `institution` current inside the block."""
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        token = set_current_institution(institution)
        try:
            yield institution
        finally:
            reset_current_institution(token)

    return _ctx()


def clear_default_cache():
    _default_ids.clear()
