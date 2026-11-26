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


# ---------- sponsor portal ----------


class SponsorRole:
    ADMIN = "sponsor_admin"
    FINANCE = "sponsor_finance"
    VIEWER = "sponsor_viewer"


SPONSOR_WRITE_ROLES = (SponsorRole.ADMIN, SponsorRole.FINANCE)
ALL_SPONSOR_ROLES = (*SPONSOR_WRITE_ROLES, SponsorRole.VIEWER)

SPONSOR_SHARING_LABELS = {
    0: "Withdrawn",
    1: "Totals only",
    2: "Coded student references",
    3: "Named student detail with consent grant",
}


def sponsor_accounts_for(user):
    """Active portal accounts for a signed-in user, newest link first."""
    if not getattr(user, "is_authenticated", False):
        return []
    from enterprise.models import SponsorAccount

    return list(
        SponsorAccount.objects.filter(
            user=user, is_active=True, sponsor__is_active=True
        ).select_related("user", "sponsor", "organisation").order_by("pk")
    )


def portal_accounts_for(user):
    """Accounts whose institution still allows the sponsor portal."""
    return [
        account for account in sponsor_accounts_for(user)
        if feature_enabled("sponsor_portal", account.sponsor.institution)
    ]


def active_sponsor_accounts_for(user):
    """Portal accounts, or 404 when the plan no longer allows the feature.

    An invited donor must not be able to tell from a permission error that the
    portal exists but is switched off, so a disabled plan looks like a missing
    page. A user with no invitation at all still gets the ordinary denial.
    """
    accounts = portal_accounts_for(user)
    if not accounts and sponsor_accounts_for(user):
        raise Http404("This feature is not enabled for this account.")
    return accounts


def sponsor_required(*roles, write=False):
    """Allow a verified sponsor account, scoped to that account's sponsor."""

    def deco(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            from django.shortcuts import redirect, reverse

            if not getattr(request.user, "is_authenticated", False):
                login_url = reverse("enterprise:sponsor_login")
                return redirect(f"{login_url}?next={request.get_full_path()}")
            accounts = active_sponsor_accounts_for(request.user)
            selected = request.session.get("sponsor_account_id")
            account = next((a for a in accounts if a.pk == selected), None)
            if account is None:
                account = accounts[0] if accounts else None
            if account is None:
                from core.audit import log_permission_denied

                log_permission_denied(request, ["sponsor_portal.use"])
                raise PermissionDenied("You do not have an active sponsor portal account.")
            if not account.is_verified:
                raise PermissionDenied(
                    "Verify your sponsor account before opening the portal."
                )
            if roles and account.role not in roles:
                raise PermissionDenied("Your sponsor role does not allow this action.")
            if write and account.role == SponsorRole.VIEWER:
                raise PermissionDenied("A sponsor viewer has read-only access.")
            request.sponsor_account = account
            request.sponsor = account.sponsor
            request.institution = account.sponsor.institution
            request.session["sponsor_account_id"] = account.pk
            return view(request, *args, **kwargs)

        return wrapper

    return deco


def require_sharing(account, level, request=None):
    """Refuse data above the sponsor's current, unwithdrawn sharing level."""
    if not account.allows(level):
        from core.audit import log_permission_denied

        if request is not None:
            log_permission_denied(request, [f"sponsor_portal.sharing_{level}"])
        raise PermissionDenied(
            "This information is outside the sharing level you agreed to. "
            "Ask the school to change it, or update it in sponsor settings."
        )
    return True


def require_identified_student_access(account, request):
    """Named student detail needs both sharing level 3 and a live grant."""
    require_sharing(account, 3, request)
    return require_student_grant(
        request, account.sponsor.institution, ("personal_data", "finance")
    )


def require_sponsor_sensitive_action(account, request):
    """Payments, notes and level-3 consent need 2FA and a fresh password."""
    from platform_admin.twofa import ready_for_sensitive_action

    message = ready_for_sensitive_action(account, request)
    if message:
        raise PermissionDenied(message)
    return True


def _harmless_sponsor_detail(value):
    allowed = {
        "sharing_level", "level", "invoice_id", "amount", "method",
        "notify_email", "notify_in_app", "sponsor_id", "kind",
    }
    return {key: value[key] for key in allowed if key in (value or {})}


def log_sponsor_action(request, action, account, detail=None, grant=None):
    """Write sponsor events to the institution and platform audit logs."""
    from core.audit import log_action
    from platform_admin.services import log_platform_action

    school = account.sponsor.institution
    safe = _harmless_sponsor_detail(detail)
    log_action(
        request.user, action, institution=school, request=request,
        model_name="SponsorAccount", object_id=str(account.pk), after=safe,
    )
    log_platform_action(
        request.user, action, institution=school, grant=grant,
        record_type="SponsorAccount", record_id=account.pk,
        detail=safe, request=request,
    )


# ---------- reseller portal ----------


class ResellerRole:
    ADMIN = "reseller_admin"
    VIEWER = "reseller_viewer"


ALL_RESELLER_ROLES = (ResellerRole.ADMIN, ResellerRole.VIEWER)


def reseller_accounts_for(user):
    """Active reseller accounts for a signed-in user."""
    if not getattr(user, "is_authenticated", False):
        return []
    from enterprise.models import Organisation, ResellerAccount

    return list(
        ResellerAccount.objects.filter(
            user=user, is_active=True,
            organisation__status=Organisation.Status.ACTIVE,
            organisation__type=Organisation.Type.RESELLER,
        ).select_related("user", "organisation").order_by("pk")
    )


def reseller_feature_enabled(account):
    """A reseller portal opens only while a client campus still has the plan."""
    return any(feature_enabled("reseller", campus) for campus in account.organisation.campuses())


def active_reseller_accounts_for(user):
    """Portal accounts, or 404 when a lapsed plan removed the feature."""
    accounts = reseller_accounts_for(user)
    eligible = [account for account in accounts if reseller_feature_enabled(account)]
    if not eligible and accounts:
        raise Http404("This feature is not enabled for this account.")
    return eligible


def reseller_required(*roles):
    """Allow a reseller account, scoped to the organisation in the URL."""

    def deco(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            from django.shortcuts import redirect, reverse

            slug = kwargs.get("slug", "")
            if not getattr(request.user, "is_authenticated", False):
                login_url = reverse("enterprise:reseller_login", args=[slug])
                return redirect(f"{login_url}?next={request.get_full_path()}")
            accounts = active_reseller_accounts_for(request.user)
            account = next(
                (a for a in accounts if a.organisation.slug == slug), None
            )
            if account is None:
                from core.audit import log_permission_denied

                log_permission_denied(request, ["reseller_portal.use"])
                raise PermissionDenied("You do not hold a role with this reseller.")
            if roles and account.role not in roles:
                raise PermissionDenied("Your reseller role does not allow this action.")
            request.reseller_account = account
            request.organisation = account.organisation
            request.session["reseller_account_id"] = account.pk
            return view(request, *args, **kwargs)

        return wrapper

    return deco
