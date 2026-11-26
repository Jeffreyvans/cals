"""Plan-gated, organisation-scoped access rules for enterprise features.

A group role is deliberately different from an institution role: it can see
campus aggregates, never student-level records. Student-level access goes
through the existing consent grant and is checked again at the view.
"""

from functools import wraps

from django.core.exceptions import PermissionDenied
from django.http import Http404

FEATURE_CODES = ("groups", "sponsor_portal", "white_label", "reseller")


def feature_enabled(code, institution=None):
    """True when the plan/subscription still allows this enterprise feature."""
    if code not in FEATURE_CODES:
        return False
    from core.models import Institution
    from core.modules import is_enabled
    from core.tenancy import current_institution_id
    from platform_admin.models import Subscription

    if institution is None:
        institution = Institution.objects.filter(pk=current_institution_id()).first()
    if institution is None:
        return False
    subscription = Subscription.objects.filter(institution=institution).order_by(
        "-created", "-pk"
    ).first()
    if subscription is not None and not subscription.is_current:
        return False
    if is_enabled(code, institution):
        return True
    return bool(
        subscription is not None
        and subscription.plan is not None
        and code in (subscription.plan.modules or [])
    )


def feature_required(code):
    """Hide an enterprise view when the plan does not allow the feature."""

    def deco(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            institution = getattr(request, "institution", None)
            if institution is None:
                user = getattr(request, "user", None)
                if user is not None and getattr(user, "is_authenticated", False):
                    institution = getattr(user, "institution", None)
            if not feature_enabled(code, institution):
                raise Http404("This feature is not enabled for this account.")
            return view(request, *args, **kwargs)

        return wrapper

    return deco


def group_role(user, organisation):
    """The active group role held by a user in one organisation, or None."""
    if not getattr(user, "is_authenticated", False):
        return None
    from enterprise.models import GroupMembership

    row = GroupMembership.objects.filter(
        user=user, organisation=organisation, is_active=True
    ).select_related("organisation").first()
    return row


def group_required(*roles, write=False):
    """Allow a group role for the organisation in the URL.

    ``write`` excludes the read-only auditor. Group roles never grant student
    data access; views that need it must call ``student_level_access`` too.
    """

    def deco(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            from django.shortcuts import get_object_or_404, redirect, reverse

            from enterprise.models import Organisation

            slug = kwargs["slug"]
            if not getattr(request.user, "is_authenticated", False):
                login_url = reverse("enterprise:group_login", args=[slug])
                return redirect(f"{login_url}?next={request.get_full_path()}")
            organisation = get_object_or_404(
                Organisation, slug=slug, status=Organisation.Status.ACTIVE
            )
            membership = group_role(request.user, organisation)
            if membership is None or (roles and membership.role not in roles):
                raise PermissionDenied("You do not hold a role in this organisation.")
            if write and membership.role == GroupRole.AUDITOR:
                raise PermissionDenied("A group auditor has read-only access.")
            request.organisation = organisation
            request.group_membership = membership
            # Group features are scoped to the campus chosen at sign-in, not
            # to the person's original home campus.
            campus = organisation.campuses().filter(
                pk=request.session.get("group_campus_id")
            ).first() or organisation.campuses().first()
            if campus is not None:
                request.institution = campus
            return view(request, *args, **kwargs)

        return wrapper

    return deco


# Convenience constants so callers do not spell role codes in view modules.
class GroupRole:
    ADMIN = "group_admin"
    FINANCE = "group_finance"
    ACADEMIC = "group_academic"
    AUDITOR = "group_auditor"


ALL_GROUP_ROLES = (GroupRole.ADMIN, GroupRole.FINANCE, GroupRole.ACADEMIC, GroupRole.AUDITOR)
WRITE_GROUP_ROLES = (GroupRole.ADMIN, GroupRole.FINANCE, GroupRole.ACADEMIC)


def _harmless_detail(value):
    """Group events never record request bodies, tokens or student names."""
    allowed = {"campus_ids", "key", "mode", "categories", "hours", "roles"}
    return {key: value[key] for key in allowed if key in (value or {})}


def log_group_action(request, action, organisation, institution=None, detail=None, grant=None):
    """Write to the institution audit and the platform's access log."""
    from core.audit import log_action
    from platform_admin.services import log_platform_action

    log_action(
        request.user, action, institution=institution, request=request,
        model_name="Organisation", object_id=str(organisation.pk),
        after=_harmless_detail(detail),
    )
    log_platform_action(
        request.user, action, institution=institution, grant=grant,
        record_type="Organisation", record_id=organisation.pk,
        detail=_harmless_detail(detail), request=request,
    )


def student_level_grant(user, institution, categories=("personal_data", "results", "finance"),
                        request=None):
    """The active, scoped consent grant for student-level group access."""
    from platform_admin.access import grant_covers
    from platform_admin.services import active_grant_for, grant_for_request

    grant = (
        grant_for_request(user, institution, request=request)
        if request is not None else active_grant_for(user, institution)
    )
    if not grant_covers(grant, set(categories)):
        return None
    return grant


def require_student_grant(request, institution, categories=("personal_data",)):
    """Refuse a student-level page without an approved group consent grant."""
    grant = student_level_grant(request.user, institution, categories, request=request)
    if grant is None:
        from core.audit import log_permission_denied

        log_permission_denied(request, ["students.view_all"])
        raise PermissionDenied(
            "Student-level data needs the campus administrator's consent. "
            "Ask for a time-limited grant before opening this page (GRANT-REQUIRED)."
        )
    from platform_admin.services import log_platform_action

    log_platform_action(
        request.user, "platform.view", institution=institution, grant=grant,
        record_type="Student", record_id="", request=request,
    )
    return grant
