"""Current-institution handling.

Every tenant table carries an ``institution`` foreign key. The current
institution is resolved in this order:

1. the value set for this request (middleware, tests, background jobs),
2. the default institution (the single school a fresh install starts with).

Reads through :class:`core.models.TenantManager` are filtered by the current
institution, so one institution can never see another institution's rows.
"""

from contextvars import ContextVar

from django.db import connections, models, router

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


def institution_is_scoped():
    """True when an institution has been set for the current scope.

    A request, a service or a test that sets the institution gets tenant
    filtering. Code that never sets one is treated as platform code: it may
    read across institutions, but new rows still default to the installation's
    default institution.
    """
    return _current_institution.get() is not None


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


class CurrentInstitutionId(models.Func):
    """The institution that is current when the query is compiled.

    Filtering with this expression keeps the tenant filter lazy. Django builds
    the choice list of a model form when the form class is imported, so an
    eager filter would freeze whichever institution happened to be current
    at import time into every form and every captured queryset. Reading the
    value when the SQL is built keeps the filter correct for the request,
    the background job or the test that runs it.

    When no institution has been set, the comparison is made against the row's
    own institution column. That keeps the filter in the SQL (and therefore
    keeps the laziness above) while letting platform code work across
    institutions. Requests always set an institution, so a view is still
    scoped to one school.
    """

    output_field = models.IntegerField()

    def as_sql(self, compiler, connection, **extra_context):
        if not institution_is_scoped():
            model = getattr(getattr(compiler, "query", None), "model", None)
            if model is not None:
                alias = compiler.query.get_initial_alias()
                quote = connection.ops.quote_name
                return f"({quote(alias)}.{quote('institution_id')})", []
        return "(%s)", [current_institution_id()]

