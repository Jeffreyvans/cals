"""Permission bridge between DRF and the data-driven role matrix."""

from rest_framework.permissions import BasePermission

from core.access import user_has_any_code

from .models import ApiKey


class HasApiPermission(BasePermission):
    """Allow the request when the user's role and the key's scope both allow it.

    A view declares ``required_permissions`` for reads and optionally
    ``write_permissions`` for unsafe methods. An empty list means "any signed-in
    API key", which is only used by the who-am-I endpoint.
    """

    message = "This API key does not hold the permission this endpoint needs."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not getattr(user, "is_authenticated", False):
            return False
        key = request.auth if isinstance(request.auth, ApiKey) else None
        if key is None:
            return False
        codes = getattr(view, "write_permissions", None)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if codes is None:
                codes = getattr(view, "required_permissions", ())
        else:
            codes = getattr(view, "required_permissions", ())
        codes = tuple(codes or ())
        if not codes:
            return True
        allowed = [code for code in codes if key.permits(code)]
        return bool(allowed) and user_has_any_code(user, allowed)


class IsInstitutionMember(BasePermission):
    """The authenticated user always belongs to the key's institution."""

    def has_permission(self, request, view):
        key = getattr(request, "auth", None)
        user = getattr(request, "user", None)
        if not isinstance(key, ApiKey) or user is None:
            return False
        return key.institution_id == getattr(user, "institution_id", None)
