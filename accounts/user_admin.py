"""Administrator account management: create, invite, reset and deactivate.

The public registration path creates requests. This module is the other path:
an administrator (or somebody the administrator has given permission to) adds a
person directly. Every write is tenant-scoped and audited, and an account is
never given a role above the role of the person doing the adding.
"""

from __future__ import annotations

import secrets
import string
from dataclasses import dataclass
from datetime import timedelta

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from core.audit import log_action
from core.email_types import (
    ACCOUNT_CREATED,
    FORCED_PASSWORD_CHANGE_NOTICE,
    GENERAL,
    ROLE_CHANGED,
    SET_PASSWORD_LINK,
    STAFF_INVITATION,
    STUDENT_INVITATION,
)
from core.logo import absolute_logo_url
from core.models import Department, Role, StaffProfile
from core.notifications import queue_message, render_email_html, send_message
from core.roles import LEGACY_ROLE_MAP
from core.tenancy import use_institution

from .models import User, UserInvitation
from .usernames import (
    base_from_email,
    generate_unique_username,
    generate_username_for_email,
    normalize_identifier,
    reserve_username,
    sanitize_username_base,
    validate_username,
)
from .usernames import (
    normalize_email as normalize_email_value,
)
from .validators import validate_strong_password

ACTION_RATE_LIMIT = 30
ACTION_RATE_WINDOW = 60 * 60

PASSWORD_ALPHABET = string.ascii_letters + string.digits + "!@#$%&*+-?"


@dataclass
class ProvisionedAccount:
    user: User
    access_mode: str
    password: str = ""
    invitation: UserInvitation | None = None


# ---------- capabilities ----------


def role_code_for(user):
    """The data role of a user, falling back to the legacy role column."""
    primary = getattr(user, "primary_role", None)
    if primary is not None:
        return primary.code
    return LEGACY_ROLE_MAP.get(getattr(user, "role", ""), "")


def is_institution_admin(user):
    if not getattr(user, "is_authenticated", False):
        return False
    return bool(
        getattr(user, "is_superuser", False)
        or getattr(user, "role", "") == User.Role.ADMIN
        or role_code_for(user) == "institution_admin"
    )


def can_manage_users(user):
    if not getattr(user, "is_authenticated", False):
        return False
    return is_institution_admin(user) or user.has_code("admin.manage_users")


def can_add_staff(user):
    """Administrators, and an HR officer who has been given the permission."""
    if is_institution_admin(user):
        return True
    return bool(
        can_manage_users(user)
        and role_code_for(user) in ("hr_officer", "institution_admin")
    )


def can_add_student(user):
    """Administrators, registrars and anybody with the student-create right."""
    if is_institution_admin(user):
        return True
    if role_code_for(user) == "registrar":
        return True
    return user.has_code("students.create")


def can_manage_staff_records(user):
    return is_institution_admin(user) or can_add_staff(user)


# ---------- role safety ----------


def legacy_role_for(role):
    """The legacy ``User.role`` value that matches a data role."""
    if role is None:
        return User.Role.STAFF
    if role.code == "institution_admin":
        return User.Role.ADMIN
    for legacy, code in LEGACY_ROLE_MAP.items():
        if code == role.code:
            if legacy == "accountant":
                return User.Role.ACCOUNTANT
            return legacy
    return User.Role.STAFF


def _role_permission_codes(role):
    return set(
        role.role_permissions.values_list("permission__code", flat=True)
    )


def assignable_roles(user):
    """Roles this person may give somebody else.

    Nobody can grant a role with a permission they do not hold themselves, and
    only an institution administrator can create another administrator.
    """
    if not can_manage_users(user):
        return Role.all_objects.none()
    roles = Role.all_objects.filter(institution=user.institution_id).order_by("name")
    if is_institution_admin(user):
        return roles
    mine = set(user.permission_codes())
    safe_ids = []
    for role in roles.exclude(code="institution_admin"):
        if _role_permission_codes(role) <= mine:
            safe_ids.append(role.pk)
    return roles.filter(pk__in=safe_ids)


def check_role_allowed(actor, role):
    if role is None:
        raise ValidationError("Choose a role for this account.")
    if role.institution_id != getattr(actor, "institution_id", None):
        raise ValidationError("Choose a role from this institution.")
    if role.code == "institution_admin" and not is_institution_admin(actor):
        raise ValidationError("Only an institution administrator can create another administrator.")
    if not is_institution_admin(actor):
        mine = set(actor.permission_codes())
        if not _role_permission_codes(role) <= mine:
            raise ValidationError(
                "You cannot grant a role with more permissions than your own."
            )


# ---------- names, usernames and passwords ----------


def normalize_username(value):
    """Normalise a username to the platform's lowercase key form."""
    return normalize_identifier(value).lower()[:150]


def unique_username(base, email=""):
    """Return a unique, reserved username following the platform rules."""
    seed = sanitize_username_base(base) or base_from_email(email)
    return generate_unique_username(email=email, base=seed, fallback="user")


def normalized_email(value):
    return normalize_email_value(value)


def _is_staff_or_admin_user(user=None, role=None):
    if role is not None:
        return legacy_role_for(role) != User.Role.STUDENT and getattr(role, "code", "") != "parent"
    if user is None:
        return False
    if getattr(user, "role", "") in (User.Role.STUDENT, User.Role.PARENT):
        return False
    if getattr(user, "primary_role_id", None):
        return legacy_role_for(user.primary_role) != User.Role.STUDENT and getattr(user.primary_role, "code", "") != "parent"
    return user.role != User.Role.STUDENT


def validate_user_email(email, institution=None, exclude_user=None, staff_or_admin=False):
    """Normalise, validate format and enforce per-institution staff/admin uniqueness."""
    email = normalize_email_value(email)
    if not email:
        return ""
    try:
        validate_email(email)
    except ValidationError:
        raise ValidationError("Enter a valid email address.") from None
    if staff_or_admin and institution is not None:
        from accounts.models import User

        staff_queryset = (
            User.objects.filter(institution=institution, email__iexact=email, is_active=True)
            .exclude(role__in=(User.Role.STUDENT, User.Role.PARENT))
        )
        if exclude_user is not None:
            staff_queryset = staff_queryset.exclude(pk=exclude_user.pk)
        if staff_queryset.exists():
            raise ValidationError("An account at this institution already uses that email address.")
    return email


def generate_temporary_password(length=14):
    """A strong one-time password that is easy to read out loud."""
    required = (
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice("!@#$%&*+-?"),
    )
    rest = [secrets.choice(PASSWORD_ALPHABET) for _ in range(max(4, length - 4))]
    characters = list(required) + rest
    secrets.SystemRandom().shuffle(characters)
    return "".join(characters)


def action_rate_limited(actor, scope, limit=ACTION_RATE_LIMIT, window=ACTION_RATE_WINDOW):
    return cache.get(_rate_key(actor, scope), 0) >= limit


def record_action(actor, scope, window=ACTION_RATE_WINDOW):
    key = _rate_key(actor, scope)
    cache.set(key, cache.get(key, 0) + 1, window)


def _rate_key(actor, scope):
    return f"edusphere.useradmin.{scope}.{getattr(actor, 'pk', 'anon')}"


# ---------- email ----------


def _send_email(user, subject, body, related_object=None, html_body="",
                email_type=GENERAL):
    if not user.email:
        return None
    message = queue_message(
        "email",
        user.email,
        body,
        subject=subject,
        institution=user.institution,
        related_object=related_object or user,
        html_body=html_body,
        email_type=email_type,
    )
    send_message(message)
    return message


def _invitation_link(invitation, request=None):
    from core.notifications import absolute_url

    return absolute_url(reverse("accept_invitation", args=[invitation.token]))


def _login_link(user):
    from core.notifications import absolute_url

    institution = getattr(user, "institution", None)
    if institution is not None and getattr(institution, "slug", ""):
        return absolute_url(reverse("institution_login", args=[institution.slug]))
    return absolute_url(reverse("login"))


def _sign_in_instruction(user):
    return (
        f"Sign in at {_login_link(user)} with your email or username {user.username}."
    )


def send_username_changed_notice(user, old_username, new_username):
    """Tell a person their login name changed. Never contains a password."""
    return _send_email(
        user,
        "Your EduSphere username has changed",
        (
            f"Hello {user.get_full_name() or user.username},\n\n"
            f"Your username has changed from {old_username} to {new_username}.\n"
            f"{_sign_in_instruction(user)}\n\n"
            "If you did not expect this change, contact your institution.\n"
        ),
        email_type=GENERAL,
    )


def _invitation_body(user, invitation, request=None):
    institution = user.institution
    return (
        f"Hello {user.get_full_name() or user.username},\n\n"
        f"{institution.name} has created an account for you on EduSphere.\n\n"
        f"{_sign_in_instruction(user)}\n"
        f"Choose your password here: {_invitation_link(invitation, request)}\n\n"
        "The link can be used once and expires in 72 hours. If it expires, "
        "ask the institution to send a new invitation.\n"
    )


def _invitation_html(user, invitation, request=None):
    return render_email_html(
        "emails/invitation.html",
        {
            "user": user,
            "institution": user.institution,
            "link": _invitation_link(invitation, request),
            "login_link": _login_link(user),
            "logo_url": absolute_logo_url(user.institution, size=160),
        },
    )


def create_invitation(user, actor=None, request=None, send=True, email_type=None):
    """Create a fresh invitation and email it. Previous open links are closed."""
    UserInvitation.objects.filter(user=user, used_at__isnull=True).update(
        used_at=timezone.now()
    )
    invitation = UserInvitation.objects.create(
        user=user,
        invited_by=actor if getattr(actor, "is_authenticated", False) else None,
        expires_at=timezone.now() + timedelta(hours=UserInvitation.LIFETIME_HOURS),
    )
    if send:
        invitation_type = email_type or (
            STUDENT_INVITATION if getattr(user, "role", "") == "student"
            else STAFF_INVITATION
        )
        _send_email(
            user,
            f"Your {user.institution.name} account",
            _invitation_body(user, invitation, request),
            related_object=invitation,
            html_body=_invitation_html(user, invitation, request),
            email_type=invitation_type,
        )
    return invitation


def send_account_created_notice(user):
    if not user.email:
        return None
    return _send_email(
        user,
        f"Your {user.institution.name} account has been created",
        (
            f"Hello {user.get_full_name() or user.username},\n\n"
            f"An account has been created for you at {user.institution.name}.\n"
            f"{_sign_in_instruction(user)}\n\n"
            "If you were given a temporary password, sign in and change it. "
            "If not, use the invitation link that was sent separately.\n"
        ),
        email_type=ACCOUNT_CREATED,
    )


def send_role_changed_notice(user, role):
    return _send_email(
        user,
        f"Your role at {user.institution.name} has changed",
        (
            f"Hello {user.get_full_name() or user.username},\n\n"
            f"Your role is now {role.name}. The change applies the next time "
            "you open a page.\n"
        ),
        email_type=ROLE_CHANGED,
    )


def send_password_reset_notice(user):
    return _send_email(
        user,
        f"Your {user.institution.name} password was reset",
        (
            f"Hello {user.get_full_name() or user.username},\n\n"
            "An administrator reset your password.\n"
            f"{_sign_in_instruction(user)}\n\n"
            "Sign in with the temporary password you were given and choose a new one.\n"
            "If you did not expect this, contact the institution.\n"
        ),
        email_type=FORCED_PASSWORD_CHANGE_NOTICE,
    )


# ---------- creating an account ----------


def _normalize_access_mode(access_mode):
    return {
        "invite": "invitation",
        "temp_password": "temporary_password",
        "none": "no_login",
    }.get(access_mode, access_mode)


@transaction.atomic
def provision_user(
    institution,
    *,
    first_name,
    last_name,
    email="",
    phone="",
    username="",
    role=None,
    access_mode="invitation",
    actor=None,
    request=None,
    send_email=True,
):
    """Create a login in one of the access modes.

    Invitation: an unusable password plus a one-time 72-hour link.
    Temporary password: a strong random password shown once to the administrator
    and a forced change at the next sign-in.
    No login: an account with an unusable password and no invitation yet.

    An invitation to a person without an email falls back to a temporary
    password, because there is nowhere to send the link. Bulk imports pass
    ``send_email=False`` so invitations can be queued and rate limited later.
    """
    if role is None:
        role = Role.all_objects.filter(institution=institution, code="lecturer").first()
    staff_or_admin = _is_staff_or_admin_user(role=role)
    email = validate_user_email(
        email, institution=institution, staff_or_admin=staff_or_admin
    )

    explicit_username = normalize_identifier(username)
    if explicit_username:
        username = validate_username(explicit_username)
        reserve_username(username)
    else:
        username = generate_username_for_email(email, f"{first_name} {last_name}")

    access_mode = _normalize_access_mode(access_mode)
    if access_mode not in ("invitation", "temporary_password", "no_login"):
        raise ValidationError("Choose how the new user gets access.")
    if access_mode == "invitation" and not email:
        access_mode = "temporary_password"

    # Enforce per-institution staff/admin email uniqueness for staff/admin accounts.
    if email and staff_or_admin:
        validate_user_email(
            email, institution=institution, staff_or_admin=True
        )

    user = User(
        username=username,
        email=email,
        first_name=str(first_name or "").strip()[:150],
        last_name=str(last_name or "").strip()[:150],
        phone=str(phone or "").strip()[:30],
        role=legacy_role_for(role),
        primary_role=role,
        institution=institution,
        is_active=True,
        must_change_password=access_mode == "temporary_password",
    )

    password = ""
    invitation = None
    if access_mode == "temporary_password":
        password = generate_temporary_password()
        user.set_password(password)
    else:
        user.set_unusable_password()

    user.save()
    reserve_username(user.username)

    if send_email:
        if access_mode == "invitation":
            invitation = create_invitation(user, actor=actor, request=request)
        elif access_mode == "temporary_password" and email:
            send_account_created_notice(user)

    log_action(
        actor,
        "user.created",
        instance=user,
        request=request,
        after={
            "username": user.username,
            "role": getattr(role, "code", ""),
            "access_mode": access_mode,
        },
    )
    return ProvisionedAccount(
        user=user,
        access_mode=access_mode,
        password=password,
        invitation=invitation,
    )


@transaction.atomic
def add_staff_member(
    institution,
    *,
    full_name,
    email,
    phone="",
    username="",
    role,
    departments=(),
    job_title="",
    staff_no="",
    start_date=None,
    access_mode="invitation",
    actor=None,
    request=None,
):
    """Create the login, staff profile and role assignment together."""
    actor_institution = getattr(actor, "institution_id", None)
    if actor_institution is not None and actor_institution != institution.pk:
        raise ValidationError("You cannot add staff to another institution.")
    check_role_allowed(actor, role)
    full_name = " ".join(str(full_name or "").split())
    if not full_name:
        raise ValidationError("Enter the person's full name.")
    email = validate_user_email(email, institution=institution, staff_or_admin=True)
    if access_mode == "invitation" and not email:
        raise ValidationError("Enter an email address, or use a temporary password.")

    department_ids = [d.pk for d in departments if d is not None]
    for department in Department.all_objects.filter(pk__in=department_ids):
        if department.institution_id != institution.pk:
            raise ValidationError("Choose departments from this institution.")

    first_name, _, last_name = full_name.partition(" ")
    with use_institution(institution):
        if staff_no:
            from core.numbering import validate_manual_number

            staff_no = validate_manual_number(
                institution, "staff", staff_no, actor=actor, request=request)
        account = provision_user(
            institution,
            first_name=first_name,
            last_name=last_name or first_name,
            email=email,
            phone=phone,
            username=username,
            role=role,
            access_mode=access_mode,
            actor=actor,
            request=request,
        )
        profile = StaffProfile(
            institution=institution,
            user=account.user,
            employee_no=str(staff_no or "").strip(),
            job_title=str(job_title or "").strip()[:120],
            phone=str(phone or "").strip()[:30],
            date_joined=start_date,
        )
        first_department = next(
            (d for d in departments if d is not None), None)
        if first_department is not None:
            profile._department_code = first_department.code
        profile.save()
        if department_ids:
            profile.departments.set(
                Department.all_objects.filter(
                    institution=institution, pk__in=department_ids
                )
            )
        log_action(
            actor,
            "staff.created",
            instance=profile,
            request=request,
            after={"user": account.user.pk, "employee_no": profile.employee_no},
        )
        return account


# ---------- changing accounts ----------


def _is_admin_account(user):
    return user.role == User.Role.ADMIN or role_code_for(user) == "institution_admin"


def active_administrators(institution, exclude=None):
    # Use the unfiltered manager: this check may run on a system path where the
    # current institution has not been set, and a missed administrator would
    # let the last admin be deactivated.
    queryset = User.objects.filter(
        institution=institution,
        is_active=True,
    ).filter(
        Q(role=User.Role.ADMIN) | Q(primary_role__code="institution_admin")
    )
    if exclude is not None:
        queryset = queryset.exclude(pk=exclude.pk)
    return queryset


def ensure_not_last_administrator(user):
    if not _is_admin_account(user):
        return
    if not active_administrators(user.institution, exclude=user).exists():
        raise ValidationError(
            "This is the last active administrator. Add another administrator first."
        )


@transaction.atomic
def update_user(user, *, first_name, last_name, email, phone, username=None, actor=None, request=None):
    if user.institution_id != getattr(actor, "institution_id", None):
        raise ValidationError("That account belongs to another institution.")

    # A bound ModelForm may already have copied submitted values onto the
    # instance. Read the stored row so "before" and the change comparison are
    # based on what is actually in the database.
    current = User.objects.get(pk=user.pk)

    staff_or_admin = _is_staff_or_admin_user(user=user)
    email = validate_user_email(
        email, institution=user.institution, exclude_user=user, staff_or_admin=staff_or_admin
    )

    before = {
        "first_name": current.first_name,
        "last_name": current.last_name,
        "email": current.email,
        "phone": current.phone,
        "username": current.username,
    }

    update_fields = ["first_name", "last_name", "email", "phone"]
    username_changed = False
    if username is not None:
        explicit_username = normalize_identifier(username)
        if not explicit_username:
            raise ValidationError("Choose a username.")
        # Only an institution administrator may change a username.
        if not is_institution_admin(actor):
            raise ValidationError("Only an institution administrator can change usernames.")
        new_username = validate_username(explicit_username, exclude_user=current)
        reserve_username(new_username)
        if new_username != current.username:
            reserve_username(current.username)
            user.username = new_username
            username_changed = True
            update_fields.append("username")

    user.first_name = str(first_name or "").strip()[:150]
    user.last_name = str(last_name or "").strip()[:150]
    user.email = email
    user.phone = str(phone or "").strip()[:30]
    user.save(update_fields=list(dict.fromkeys(update_fields)))
    log_action(
        actor,
        "user.updated",
        instance=user,
        before=before,
        request=request,
        after={
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "phone": user.phone,
            "username": user.username,
        },
    )
    if username_changed:
        send_username_changed_notice(user, before["username"], user.username)
    return user


def _same_institution(actor, user):
    """True when the actor may act on this account.

    Services are also called by admissions and by the import runner, where the
    actor is always set. A system call without an actor is allowed; a real
    actor is never allowed to reach another institution's rows.
    """
    actor_institution = getattr(actor, "institution_id", None)
    if actor_institution is None:
        return True
    return user.institution_id == actor_institution


@transaction.atomic
def change_user_role(user, role, actor=None, request=None):
    if not _same_institution(actor, user):
        raise ValidationError("That account belongs to another institution.")
    check_role_allowed(actor, role)
    if (
        user.pk == getattr(actor, "pk", None)
        and _is_admin_account(user)
        and role.code != "institution_admin"
    ):
        raise ValidationError("You cannot remove your own administrator role.")
    was_admin = _is_admin_account(user)
    will_be_admin = role.code == "institution_admin"
    if was_admin and not will_be_admin:
        ensure_not_last_administrator(user)
    will_be_staff_or_admin = legacy_role_for(role) != User.Role.STUDENT and getattr(role, "code", "") != "parent"
    if will_be_staff_or_admin and getattr(user, "email", ""):
        validate_user_email(
            user.email,
            institution=user.institution,
            exclude_user=user,
            staff_or_admin=True,
        )
    before = {"role": getattr(user.primary_role, "code", "") or user.role}
    user.primary_role = role
    user.role = legacy_role_for(role)
    user.save(update_fields=["primary_role", "role"])
    log_action(
        actor,
        "user.role_changed",
        instance=user,
        before=before,
        after={"role": role.code},
        request=request,
    )
    send_role_changed_notice(user, role)
    return user


@transaction.atomic
def deactivate_user(user, actor=None, request=None, reason=""):
    if not _same_institution(actor, user):
        raise ValidationError("That account belongs to another institution.")
    if user.pk == getattr(actor, "pk", None):
        raise ValidationError("You cannot deactivate your own account.")
    ensure_not_last_administrator(user)
    if not user.is_active:
        return user
    user.is_active = False
    user.deactivated_at = timezone.now()
    UserInvitation.objects.filter(user=user, used_at__isnull=True).update(
        used_at=timezone.now()
    )
    user.save(update_fields=["is_active", "deactivated_at"])
    log_action(
        actor,
        "user.deactivated",
        instance=user,
        after={"reason": str(reason or "")[:500]},
        request=request,
    )
    return user


@transaction.atomic
def reactivate_user(user, actor=None, request=None):
    if not _same_institution(actor, user):
        raise ValidationError("That account belongs to another institution.")
    user.is_active = True
    user.deactivated_at = None
    user.failed_login_count = 0
    user.locked_until = None
    user.save(update_fields=["is_active", "deactivated_at", "failed_login_count", "locked_until"])
    log_action(actor, "user.reactivated", instance=user, request=request)
    return user


@transaction.atomic
def unlock_user(user, actor=None, request=None):
    if not _same_institution(actor, user):
        raise ValidationError("That account belongs to another institution.")
    user.failed_login_count = 0
    user.locked_until = None
    user.save(update_fields=["failed_login_count", "locked_until"])
    log_action(actor, "user.unlocked", instance=user, request=request)
    return user


@transaction.atomic
def reset_password(user, actor=None, request=None):
    """Set a one-time password, force a change and return it to the caller."""
    if not _same_institution(actor, user):
        raise ValidationError("That account belongs to another institution.")
    if action_rate_limited(actor, "reset"):
        raise ValidationError("Too many password resets. Wait a few minutes and try again.")
    password = generate_temporary_password()
    user.set_password(password)
    user.must_change_password = True
    user.password_set_from_env = False
    user.failed_login_count = 0
    user.locked_until = None
    user.save(update_fields=["password", "must_change_password",
                             "password_set_from_env", "failed_login_count",
                             "locked_until"])
    record_action(actor, "reset")
    log_action(actor, "user.password_reset", instance=user, request=request)
    send_password_reset_notice(user)
    return password


@transaction.atomic
def resend_invitation(user, actor=None, request=None):
    if not _same_institution(actor, user):
        raise ValidationError("That account belongs to another institution.")
    if action_rate_limited(actor, "invite"):
        raise ValidationError("Too many invitations. Wait a few minutes and try again.")
    if not user.email:
        raise ValidationError("This account has no email address. Use a temporary password.")
    invitation = create_invitation(
        user, actor=actor, request=request, email_type=SET_PASSWORD_LINK)
    record_action(actor, "invite")
    log_action(actor, "user.invitation_resent", instance=user, request=request)
    return invitation


@transaction.atomic
def accept_invitation(invitation, password, request=None):
    """Set the password for a valid invitation. Returns the user."""
    if not invitation.is_open:
        raise ValidationError("This invitation has already been used or has expired.")
    try:
        validate_strong_password(password, username=invitation.user.username,
                                 email=invitation.user.email)
    except ValidationError as exc:
        raise ValidationError(exc.messages) from exc
    user = invitation.user
    user.set_password(password)
    user.must_change_password = False
    user.password_set_from_env = False
    user.is_active = True
    user.failed_login_count = 0
    user.locked_until = None
    user.save(update_fields=["password", "must_change_password",
                             "password_set_from_env", "is_active",
                             "failed_login_count", "locked_until"])
    invitation.used_at = timezone.now()
    invitation.save(update_fields=["used_at"])
    log_action(user, "user.invitation_accepted", instance=user, request=request)

    # The first administrator of an approved institution chooses the password
    # that makes the institution live. Every later invitation changes nothing.
    institution = getattr(user, "institution", None)
    source_application = getattr(institution, "source_application", None) if institution else None
    if (
        institution is not None
        and source_application is not None
        and source_application.status == "approved"
        and not institution.is_active
        and institution.suspended_at is None
        and institution.archived_at is None
        and institution.deleted_at is None
        and user.role == User.Role.ADMIN
    ):
        institution.is_active = True
        institution.save(update_fields=["is_active"])
        log_action(
            user,
            "institution.activated_by_first_administrator",
            institution=institution,
            after={"reference": source_application.reference},
            request=request,
        )
    return user


# ---------- linking and duplicates ----------


@transaction.atomic
def attach_login(student, actor=None, request=None, access_mode="invitation"):
    """Give an existing student record a usable login."""
    if student.institution_id != getattr(actor, "institution_id", None):
        raise ValidationError("That student belongs to another institution.")
    user = student.user
    if user is None:
        raise ValidationError("That student record has no login to attach.")
    if user.is_active and user.has_usable_password() and not user.must_change_password:
        raise ValidationError("That student already has a working login.")
    user.is_active = True
    user.save(update_fields=["is_active"])
    log_action(actor, "user.login_linked", instance=user, request=request)
    access_mode = _normalize_access_mode(access_mode)
    if access_mode == "temporary_password":
        password = reset_password(user, actor=actor, request=request)
        return ProvisionedAccount(user=user, access_mode="temporary_password", password=password)
    invitation = resend_invitation(user, actor=actor, request=request)
    return ProvisionedAccount(user=user, access_mode="invitation", invitation=invitation)


@transaction.atomic
def link_parent(parent, student, relationship="", can_view_finance=True,
                actor=None, request=None):
    from academics.models import Guardian

    if parent.institution_id != student.institution_id:
        raise ValidationError("The parent and student must be in the same institution.")
    if parent.institution_id != getattr(actor, "institution_id", None):
        raise ValidationError("Choose a parent and student from this institution.")
    link, created = Guardian.objects.get_or_create(
        parent=parent,
        student=student,
        defaults={
            "relationship": str(relationship or "").strip()[:40],
            "can_view_finance": bool(can_view_finance),
        },
    )
    if not created:
        link.relationship = str(relationship or "").strip()[:40]
        link.can_view_finance = bool(can_view_finance)
        link.save(update_fields=["relationship", "can_view_finance"])
    log_action(actor, "user.parent_linked", instance=link, request=request,
               after={"parent": parent.pk, "student": student.pk})
    return link


@transaction.atomic
def flag_duplicate(user, primary, actor=None, request=None):
    if not _same_institution(actor, user):
        raise ValidationError("That account belongs to another institution.")
    if user.pk == primary.pk:
        raise ValidationError("Choose two different accounts.")
    if user.institution_id != primary.institution_id:
        raise ValidationError("Both accounts must be in the same institution.")
    user.duplicate_of = primary
    user.is_active = False
    user.deactivated_at = timezone.now()
    user.save(update_fields=["duplicate_of", "is_active", "deactivated_at"])
    log_action(actor, "user.duplicate_flagged", instance=user, request=request,
               after={"duplicate_of": primary.pk})
    return user


@transaction.atomic
def merge_users(primary, duplicate, actor=None, request=None):
    """Fold a duplicate into the primary account without deleting history.

    Links that point at the duplicate are moved where that is safe. Financial
    and academic rows are never deleted; if the duplicate has its own student
    record we keep it and flag the login instead of guessing which history wins.
    """
    from academics.models import Guardian, Student

    if duplicate.pk == primary.pk:
        raise ValidationError("Choose two different accounts.")
    if not _same_institution(actor, duplicate):
        raise ValidationError("That account belongs to another institution.")
    if duplicate.institution_id != primary.institution_id:
        raise ValidationError("Both accounts must be in the same institution.")
    if duplicate.pk == getattr(actor, "pk", None):
        raise ValidationError("You cannot merge your own account.")

    duplicate_student = Student.all_objects.filter(user=duplicate).first()
    primary_student = Student.all_objects.filter(user=primary).first()
    if duplicate_student is not None:
        has_history = (
            duplicate_student.invoices.exists()
            or duplicate_student.enrollments.exists()
        )
        if primary_student is not None or has_history:
            return flag_duplicate(duplicate, primary, actor=actor, request=request)
        duplicate_student.user = primary
        duplicate_student.save(update_fields=["user"])

    for link in Guardian.objects.filter(parent=duplicate):
        existing = Guardian.objects.filter(parent=primary, student=link.student).first()
        if existing is None:
            link.parent = primary
            link.save(update_fields=["parent"])
        else:
            link.delete()

    duplicate.duplicate_of = primary
    duplicate.is_active = False
    duplicate.deactivated_at = timezone.now()
    duplicate.save(update_fields=["duplicate_of", "is_active", "deactivated_at"])
    log_action(actor, "user.merged", instance=duplicate, request=request,
               after={"into": primary.pk})
    return primary


def deactivation_has_history(user):
    """True when the account has money, results or audit rows behind it."""
    from academics.models import Student

    student = Student.all_objects.filter(user=user).first()
    if student is not None:
        if student.invoices.exists() or student.enrollments.exists():
            return True
    return user.audit_entries.exists()


# ---------- login lock ----------

LOGIN_FAILURE_LIMIT = 8
LOGIN_LOCK_MINUTES = 15


def note_failed_login(user, request=None):
    if user is None:
        return
    user.failed_login_count = (user.failed_login_count or 0) + 1
    update = ["failed_login_count"]
    if user.failed_login_count >= LOGIN_FAILURE_LIMIT:
        user.locked_until = timezone.now() + timedelta(minutes=LOGIN_LOCK_MINUTES)
        user.failed_login_count = 0
        update = ["failed_login_count", "locked_until"]
        log_action(None, "user.locked", instance=user, request=request)
    user.save(update_fields=update)


def clear_failed_logins(user):
    if user and (user.failed_login_count or user.locked_until):
        user.failed_login_count = 0
        user.locked_until = None
        user.save(update_fields=["failed_login_count", "locked_until"])


def is_locked(user):
    return bool(user and user.locked_until and user.locked_until > timezone.now())
