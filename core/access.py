"""Permission checks.

Roles are data, so a check asks for a code, not a role name. The decorator
:class:`permission_required_code` protects a view; :func:`user_has_code` is the
same check for templates and services.
"""

from functools import wraps

from django.core.exceptions import PermissionDenied
from django.db import models
from django.http import Http404


def _roles_for_user(user):
    """The roles the user holds inside their own institution."""
    from core.models import Role

    institution_id = getattr(user, "institution_id", None)
    if institution_id is None:
        return Role.all_objects.none()
    roles = Role.all_objects.filter(institution_id=institution_id)
    ids = set()
    primary = getattr(user, "primary_role", None)
    if primary is not None:
        ids.add(primary.pk)
    extra = getattr(user, "extra_roles", None)
    if extra is not None:
        ids.update(extra.values_list("pk", flat=True))
    legacy_code = None
    legacy_role = getattr(user, "role", "")
    if legacy_role:
        from core.roles import LEGACY_ROLE_MAP

        legacy_code = LEGACY_ROLE_MAP.get(legacy_role)
    if not ids and legacy_code is None:
        return Role.all_objects.none()
    q = models.Q(pk__in=ids)
    if legacy_code:
        q = q | models.Q(code=legacy_code)
    return roles.filter(q)


def permission_codes_for(user):
    """All permission codes this user holds (cached on the user object)."""
    if not getattr(user, "is_authenticated", False):
        return set()
    cached = getattr(user, "_permission_codes", None)
    if cached is not None:
        return cached
    codes = None
    from django.conf import settings

    # Cross-request caching only outside the test suite: a test that changes a
    # role and immediately makes a request must always see the new permissions.
    if not settings.TESTING and getattr(user, "pk", None):
        from core.cache import permission_cache_key

        key = permission_cache_key(user)
        from django.core.cache import cache

        stored = cache.get(key)
        if stored is not None:
            codes = set(stored)
    if codes is None:
        if user.is_superuser:
            from core.models import RolePermission

            codes = set(RolePermission.objects.values_list("permission__code", flat=True))
        else:
            from core.models import RolePermission

            role_ids = list(_roles_for_user(user).values_list("id", flat=True))
            codes = set(
                RolePermission.objects.filter(role_id__in=role_ids).values_list(
                    "permission__code", flat=True
                )
            )
        if not settings.TESTING and getattr(user, "pk", None):
            from django.core.cache import cache

            from core.cache import permission_cache_key

            cache.set(
                permission_cache_key(user),
                sorted(codes),
                getattr(settings, "CACHE_TTL_PERMISSIONS", 300),
            )
    user._permission_codes = codes
    return codes


def user_has_code(user, code):
    """True when the user holds the permission code."""
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return code in permission_codes_for(user)


def user_has_any_code(user, codes):
    return any(user_has_code(user, code) for code in codes)


def permission_required_code(*codes):
    """Allow the view only when the user holds at least one of the codes.

    Deny by default. A denial is written to the audit log.
    """

    def deco(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            user = getattr(request, "user", None)
            if not getattr(user, "is_authenticated", False):
                return view(request, *args, **kwargs)
            if user_has_any_code(user, codes):
                return view(request, *args, **kwargs)
            from core.audit import log_permission_denied

            log_permission_denied(request, codes)
            raise PermissionDenied(
                "Your role does not allow this action. Ask an administrator for access."
            )

        return wrapper

    return deco


def module_required(*module_codes):
    """Hide a view when the institution has switched the module off."""

    def deco(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            from core.modules import is_enabled

            institution = getattr(request, "institution", None)
            for code in module_codes:
                if is_enabled(code, institution):
                    return view(request, *args, **kwargs)
            raise Http404("This module is not switched on for your institution.")

        return wrapper

    return deco
