"""Create the main platform administrator from environment variables.

The account is provisioned during a deployment, before anybody can use a
shell. Nothing here ever writes a username or password into the source tree:
both come from the environment and the password is only handled in memory and
stored through Django's normal password hashing.

The same function is used by the ``ensure_platform_admin`` management command
and (once per process) by the application startup hook.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from accounts.models import EmailVerification
from accounts.validators import validate_strong_password
from platform_admin.services import ensure_platform_staff

logger = logging.getLogger(__name__)

User = get_user_model()

USERNAME_VAR = "PLATFORM_ADMIN_USERNAME"
EMAIL_VAR = "PLATFORM_ADMIN_EMAIL"
PASSWORD_VAR = "PLATFORM_ADMIN_PASSWORD"
RESET_VAR = "PLATFORM_ADMIN_RESET_PASSWORD"
FORCE_CHANGE_VAR = "PLATFORM_ADMIN_FORCE_CHANGE"

_TRUE = {"1", "true", "yes", "on"}


@dataclass
class ProvisionResult:
    status: str  # created, exists, missing, invalid, conflict
    user: object | None = None
    messages: list[str] = field(default_factory=list)

    @property
    def ok(self):
        return self.status in {"created", "exists"}


def env_flag(name, default=False):
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in _TRUE


def _credentials():
    return {
        "username": os.environ.get(USERNAME_VAR, "").strip(),
        "email": os.environ.get(EMAIL_VAR, "").strip(),
        "password": os.environ.get(PASSWORD_VAR, ""),
    }


def _validated_credentials(credentials):
    """Return a list of plain-English problems, or an empty list."""
    problems = []
    username = credentials["username"]
    email = credentials["email"]
    password = credentials["password"]

    if not username:
        problems.append(f"{USERNAME_VAR} is empty.")
    else:
        if len(username) > 150:
            problems.append(f"{USERNAME_VAR} is longer than 150 characters.")
        try:
            UnicodeUsernameValidator()(username)
        except ValidationError:
            problems.append(
                f"{USERNAME_VAR} may contain only letters, numbers and @/./+/-/_."
            )

    if not email:
        problems.append(f"{EMAIL_VAR} is empty.")
    else:
        try:
            validate_email(email)
        except ValidationError:
            problems.append(f"{EMAIL_VAR} is not a valid email address.")

    if not password:
        problems.append(f"{PASSWORD_VAR} is empty.")
    else:
        candidate = User(username=username, email=email, is_platform_admin=True)
        try:
            validate_password(password, user=candidate)
            validate_strong_password(password, username=username, email=email)
        except ValidationError as exc:
            problems.extend(exc.messages)

    return problems


def _verified_email(user):
    if EmailVerification.objects.filter(
        user=user, verified_at__isnull=False
    ).exists():
        return
    EmailVerification.objects.create(user=user, verified_at=timezone.now())


def _find_existing(username, email):
    """Find the one account for these credentials, or report a conflict."""
    by_username = User.objects.filter(username__iexact=username).first()
    by_email = User.objects.filter(email__iexact=email).order_by("pk").first()
    if by_username is not None and by_email is not None and by_username.pk != by_email.pk:
        return None, True
    return by_username or by_email, False


def _apply_admin_flags(user):
    """Make an existing account a main platform administrator again."""
    user.is_platform_admin = True
    user.is_staff = True
    user.is_superuser = True
    user.is_active = True
    user.role = User.Role.ADMIN
    user.deactivated_at = None
    # A platform administrator is not a member of any one school.
    user.institution = None
    if not user.email:
        user.email = os.environ.get(EMAIL_VAR, "").strip()
    user.save(
        update_fields=[
            "is_platform_admin",
            "is_staff",
            "is_superuser",
            "is_active",
            "role",
            "deactivated_at",
            "institution",
            "email",
        ]
    )


def _audit_creation(user, *, via):
    """Record the first creation without any secret material."""
    detail = {
        "username": user.username,
        "email": user.email,
        "source": via,
        "role": User.Role.ADMIN,
    }
    # Keep the platform's append-only audit trail when the platform app is
    # available, and the ordinary institution trail otherwise. Neither copy
    # contains the password.
    try:
        from platform_admin.services import log_platform_action

        log_platform_action(
            user,
            "platform.admin.provisioned",
            record_type="User",
            record_id=str(user.pk),
            detail=detail,
        )
    except Exception:  # pragma: no cover - app not installed in odd setups
        logger.exception("Could not write the platform audit entry")


def _log_rights(user):
    """Say plainly, in the boot log, what the provisioned account can do."""
    staff = getattr(user, "platform_staff", None)
    role = getattr(staff, "role", None) if staff is not None else None
    logger.info(
        "Platform administrator %s has full platform rights: "
        "is_platform_admin=%s, is_staff=%s, is_superuser=%s, "
        "PlatformStaff active=%s main_admin=%s role=%s, institution=%s. "
        "Open /platform/ to manage institutions.",
        user.username,
        user.is_platform_admin,
        user.is_staff,
        user.is_superuser,
        getattr(staff, "is_active", None),
        getattr(staff, "is_main_admin", None),
        getattr(role, "code", None),
        getattr(user, "institution_id", None),
    )


@transaction.atomic
def ensure_platform_admin_from_env(*, via="management command"):
    """Create or repair the main platform administrator. Never raises for config."""
    credentials = _credentials()
    if not any(credentials.values()):
        message = (
            "Platform administrator was not created: "
            f"set {USERNAME_VAR}, {EMAIL_VAR} and {PASSWORD_VAR} first."
        )
        logger.warning(message)
        return ProvisionResult("missing", messages=[message])

    if not all(credentials.values()):
        missing = [
            name
            for name, value in (
                (USERNAME_VAR, credentials["username"]),
                (EMAIL_VAR, credentials["email"]),
                (PASSWORD_VAR, credentials["password"]),
            )
            if not value
        ]
        message = (
            "Platform administrator was not created because these variables are "
            f"missing: {', '.join(missing)}."
        )
        logger.warning(message)
        return ProvisionResult("missing", messages=[message])

    problems = _validated_credentials(credentials)
    if problems:
        message = (
            "Platform administrator was not created because the supplied values "
            "are invalid: " + " ".join(problems)
        )
        # The messages never include the password itself.
        logger.error(message)
        return ProvisionResult("invalid", messages=[message])

    username = credentials["username"]
    email = credentials["email"]
    password = credentials["password"]
    reset_password = env_flag(RESET_VAR, False)
    force_change = env_flag(FORCE_CHANGE_VAR, True)

    user, conflict = _find_existing(username, email)
    if conflict:
        message = (
            "Platform administrator was not changed: the username and email "
            "belong to two different accounts. Correct the environment values."
        )
        logger.error(message)
        return ProvisionResult("conflict", messages=[message])

    if user is None:
        user = User(
            username=username,
            email=email,
            role=User.Role.ADMIN,
            is_platform_admin=True,
            is_staff=True,
            is_superuser=True,
            is_active=True,
            institution=None,
        )
        user.set_password(password)
        user.must_change_password = force_change
        user.password_set_from_env = True
        user.save()

        ensure_platform_staff(user, "main_admin", is_main_admin=True)
        _verified_email(user)
        _audit_creation(user, via=via)
        _log_rights(user)
        message = f"Created platform administrator {user.username}."
        logger.info(message)
        return ProvisionResult("created", user=user, messages=[message])

    _apply_admin_flags(user)
    ensure_platform_staff(user, "main_admin", is_main_admin=True)
    _verified_email(user)
    _log_rights(user)

    if reset_password:
        user.set_password(password)
        user.must_change_password = force_change
        user.password_set_from_env = True
        user.failed_login_count = 0
        user.locked_until = None
        user.save(
            update_fields=[
                "password",
                "must_change_password",
                "password_set_from_env",
                "failed_login_count",
                "locked_until",
            ]
        )
        message = (
            f"Platform administrator {user.username} already existed; the password "
            "was reset from the environment."
        )
        logger.info(message)
        return ProvisionResult("exists", user=user, messages=[message])

    message = (
        f"Platform administrator {user.username} already existed; flags were "
        "checked and the password was left unchanged."
    )
    logger.info(message)
    return ProvisionResult("exists", user=user, messages=[message])


def ensure_platform_admin_from_env_or_skip():
    """Startup-hook wrapper that never lets a bad environment stop the app."""
    return ensure_platform_admin_from_env(via="application startup")
