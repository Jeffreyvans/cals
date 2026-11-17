"""Template context for the platform banner and navigation."""

from .services import active_grants_for, platform_staff_for


def platform_context(request):
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return {}
    staff = platform_staff_for(user)
    if staff is None:
        return {}
    grant = active_grants_for(user).first()
    role = staff.role
    permissions = set(role.permissions or []) if role is not None else set()
    if staff.is_main_admin:
        permissions.add("*")
    return {
        "PLATFORM_STAFF": staff,
        "PLATFORM_PERMISSIONS": permissions,
        "PLATFORM_IS_MAIN": staff.is_main_admin,
        "PLATFORM_GRANT": grant,
        "PLATFORM_GRANT_BANNER": grant.banner if grant is not None else "",
        "PLATFORM_ROLE_NAME": staff.role.name if staff.role else "Platform staff",
        # Shown until the administrator replaces the deployment password.
        "PLATFORM_PASSWORD_FROM_ENV": bool(
            getattr(user, "is_platform_admin", False)
            and getattr(user, "password_set_from_env", False)
        ),
    }
