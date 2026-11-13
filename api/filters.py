"""A small field filter so list endpoints stay dependency-free.

Views list the fields callers may filter on::

    filterset_fields = ["status", "programme", "created"]

and then any of these query parameters work::

    ?status=active
    ?status__in=active,enrolled
    ?created__gte=2026-01-01
    ?student__student_no__contains=2026
    ?reason__isnull=true

Only fields named in ``filterset_fields`` (or their ``__``-suffixed lookup) are
accepted, so a caller can never filter on a column the view did not expose.
"""

from rest_framework.filters import BaseFilterBackend

RESERVED_PARAMS = {
    "page",
    "page_size",
    "format",
    "search",
    "ordering",
    "fields",
}

LOOKUPS = ("exact", "in", "gte", "lte", "gt", "lt", "contains", "icontains", "isnull")


class SimpleFilterBackend(BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):
        allowed = getattr(view, "filterset_fields", None)
        if not allowed:
            return queryset
        allowed = set(allowed)
        for param, value in request.query_params.items():
            if param in RESERVED_PARAMS or not value:
                continue
            field, lookup = self._parse(param, allowed)
            if field is None:
                continue
            try:
                queryset = queryset.filter(**{f"{field}__{lookup}": self._coerce(lookup, value)})
            except (ValueError, TypeError):
                # An unparseable value simply matches nothing rather than
                # raising a 500 from a filter.
                return queryset.none()
        return queryset

    def _parse(self, param, allowed):
        if param in allowed:
            return param, "exact"
        # ``student__student_no__contains`` is allowed when
        # ``student__student_no`` is in ``filterset_fields``.
        for candidate in sorted(allowed, key=len, reverse=True):
            if param.startswith(candidate + "__"):
                lookup = param[len(candidate) + 2 :]
                if lookup in LOOKUPS:
                    return candidate, lookup
                return None, None
        return None, None

    def _coerce(self, lookup, value):
        if lookup == "in":
            return [item.strip() for item in value.split(",") if item.strip()]
        if lookup == "isnull":
            return value.lower() in ("1", "true", "yes")
        return value
