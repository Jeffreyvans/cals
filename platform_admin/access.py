"""Access rules for platform staff and institution consent screens."""

from __future__ import annotations

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404

from .services import (
    active_grant_for,
    grant_for_request,
    is_platform_staff,
    platform_staff_for,
    require_platform_permission,
)
from .twofa import ready_for_sensitive_action


def platform_required(*codes, main_admin=False):
    """Allow an active PlatformStaff row with one of these platform codes."""

    def deco(view):
        @wraps(view)
        @login_required
        def wrapper(request, *args, **kwargs):
            # A signed-in person with no active platform role is not told that
            # this area exists: the platform is invisible to institution users.
            if not is_platform_staff(request.user):
                raise Http404("Page not found.")
            staff = require_platform_permission(
                request.user, *codes, main_admin=main_admin
            )
            request.platform_staff = staff
            return view(request, *args, **kwargs)

        return wrapper

    return deco


def institution_admin_required(view):
    """Allow an institution administrator, never a platform-only account."""

    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        user = request.user
        if is_platform_staff(user):
            raise PermissionDenied(
                "Platform staff see institution data only through a consent grant."
            )
        institution = getattr(user, "institution", None)
        if institution is None:
            raise Http404("No institution is linked to this account.")
        allowed = (
            user.is_superuser
            or getattr(user, "role", "") == "admin"
            or user.has_code("admin.manage_settings")
            or user.has_code("admin.view_audit")
        )
        if not allowed:
            raise PermissionDenied("Only an institution administrator may use this page.")
        request.institution = institution
        return view(request, *args, **kwargs)

    return wrapper


# Prefixes of screens that hold protected institution data, and the categories
# a grant must include before the screen may open.
PROTECTED_PREFIXES = (
    ("/health/", {"health"}, "Health records"),
    ("/counselling/", {"counselling"}, "Counselling notes"),
    ("/finance/", {"finance"}, "Fees and finance"),
    ("/examinations/", {"results"}, "Results"),
    ("/academics/results", {"results"}, "Results"),
    ("/academics/reports", {"results"}, "Results"),
    ("/academics/transcript", {"results"}, "Results"),
    ("/academics/", {"personal_data", "results"}, "Student records"),
    ("/admissions/", {"personal_data"}, "Applicant records"),
    ("/office/", {"hr"}, "HR and operations"),
    ("/student-affairs/", {"hr"}, "Discipline records"),
    ("/library/", {"personal_data"}, "Library records"),
    ("/lms/", {"personal_data"}, "Learning records"),
    ("/sports/", {"personal_data"}, "Sports records"),
    ("/boarding/", {"personal_data"}, "Boarding records"),
    ("/transport/", {"personal_data"}, "Transport records"),
    ("/catering/", {"personal_data"}, "Catering records"),
    ("/careers/", {"personal_data"}, "Careers records"),
    ("/research/", {"personal_data"}, "Research records"),
    ("/labs/", {"personal_data"}, "Laboratory records"),
    ("/ict/", {"personal_data", "messages"}, "ICT records"),
    ("/settings/users", {"personal_data"}, "User accounts"),
    ("/settings/institution", {"changes"}, "Institution settings"),
    ("/imports/", {"personal_data"}, "Imported records"),
    ("/api/", {"personal_data", "results", "finance", "health", "counselling"}, "API data"),
)

EXPORT_MARKERS = ("export", "download", "/csv", ".csv", "report-card", "transcript.pdf")


def categories_for_path(path):
    path = path or ""
    for prefix, categories, label in PROTECTED_PREFIXES:
        if path.startswith(prefix):
            return set(categories), label
    return set(), ""


def is_export_path(path):
    path = (path or "").lower()
    return any(marker in path for marker in EXPORT_MARKERS)


def grant_covers(grant, categories, *, write=False, export=False):
    if grant is None or not grant.is_active:
        return False
    held = set(grant.categories or [])
    if not categories.intersection(held):
        return False
    if write:
        from .models import AccessRequest

        if grant.mode != AccessRequest.Mode.READ_WRITE or "changes" not in held:
            return False
    if export and not grant.include_export:
        return False
    return True


def platform_access_denied(request, message, categories=None):
    from .services import log_platform_action

    log_platform_action(
        request.user,
        "platform.access_denied",
        institution=getattr(request, "institution", None),
        record_type="path",
        record_id=request.path[:80],
        detail={"message": message, "categories": sorted(categories or [])},
        request=request,
    )
    raise PermissionDenied(message)


def check_platform_institution_access(request):
    """Raise 403 when platform staff open protected data without consent.

    Called by the middleware after the institution has been resolved. It logs
    every allowed view too, so the institution's "Who accessed our data" page
    has a complete trail, and it refuses to open anything unless the staff
    member has 2FA on and confirmed their password in the last 15 minutes.
    """
    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False) or not is_platform_staff(user):
        return None
    path = request.path or ""
    if path.startswith("/platform/") or path.startswith("/apply"):
        return None
    categories, label = categories_for_path(path)
    if not categories:
        return None
    institution = getattr(request, "institution", None)
    if institution is None:
        platform_access_denied(request, "Choose an institution first.", categories)
    staff = platform_staff_for(user)
    blocked = ready_for_sensitive_action(staff, request)
    if blocked:
        platform_access_denied(request, blocked, categories)
    grant = grant_for_request(user, institution, request)
    if grant is None:
        platform_access_denied(
            request,
            "This institution's data is protected. Ask the institution for consent first.",
            categories,
        )
    write = request.method not in ("GET", "HEAD", "OPTIONS")
    export = is_export_path(path)
    if not grant_covers(grant, categories, write=write, export=export):
        if export:
            message = "This consent grant does not include export."
        elif write:
            message = "This consent grant is read-only."
        else:
            message = "The consent grant does not cover this category of data."
        platform_access_denied(request, message, categories)
    from .services import log_platform_action

    action = "platform.change" if write else ("platform.export" if export else "platform.view")
    log_platform_action(
        user,
        action,
        institution=institution,
        grant=grant,
        record_type="path",
        record_id=path[:80],
        detail={"label": label, "method": request.method, "export": bool(export)},
        request=request,
    )
    # Any actual change inside the institution writes its own before/after row
    # through core.audit.log_action, which mirrors into the platform log.
    return {"grant": grant, "export": export, "institution": institution}


def require_active_grant(user, institution):
    grant = active_grant_for(user, institution)
    if grant is None:
        raise PermissionDenied("No active consent grant for this institution.")
    return grant
