"""Tests for the deployment-time platform administrator command.

Credentials are generated at run time. No real username or password is stored
in this file, and every assertion checks that the password never leaks into
the output or the logs.
"""

from __future__ import annotations

import secrets
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from accounts.models import EmailVerification
from platform_admin.models import PlatformAuditLog, PlatformStaff
from platform_admin.provisioning import (
    EMAIL_VAR,
    FORCE_CHANGE_VAR,
    PASSWORD_VAR,
    RESET_VAR,
    USERNAME_VAR,
)

User = get_user_model()


def strong_password():
    """A different strong password for each test, never a stored literal."""
    return f"Aa1!{secrets.token_urlsafe(12)}Zz9"


def credentials(**overrides):
    values = {
        USERNAME_VAR: f"platform{secrets.token_hex(4)}",
        EMAIL_VAR: f"platform{secrets.token_hex(4)}@edusphere.example",
        PASSWORD_VAR: strong_password(),
        FORCE_CHANGE_VAR: "true",
        RESET_VAR: "false",
    }
    values.update(overrides)
    return values


class EnsurePlatformAdminTests(TestCase):
    def run_command(self, env, *, allow_failure=False):
        stdout, stderr = StringIO(), StringIO()
        with patch.dict("os.environ", env, clear=False):
            args = ["ensure_platform_admin"]
            if allow_failure:
                args.append("--allow-failure")
            call_command(*args, stdout=stdout, stderr=stderr)
        return stdout.getvalue(), stderr.getvalue()

    def test_creates_the_admin_from_environment_variables(self):
        env = credentials()
        stdout, _stderr = self.run_command(env)

        user = User.objects.get(username=env[USERNAME_VAR])
        self.assertEqual(user.email, env[EMAIL_VAR])
        self.assertTrue(user.is_platform_admin)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_active)
        self.assertEqual(user.role, User.Role.ADMIN)
        self.assertIsNone(user.institution)
        self.assertTrue(user.check_password(env[PASSWORD_VAR]))
        self.assertTrue(user.must_change_password)
        self.assertTrue(user.password_set_from_env)
        self.assertIn("Created platform administrator", stdout)

        self.assertTrue(
            EmailVerification.objects.filter(
                user=user, verified_at__isnull=False
            ).exists()
        )
        staff = PlatformStaff.objects.get(user=user)
        self.assertTrue(staff.is_main_admin)
        self.assertTrue(staff.is_active)
        self.assertEqual(staff.role.code, "main_admin")
        self.assertTrue(
            PlatformAuditLog.objects.filter(action="platform.admin.provisioned").exists()
        )

    def test_running_twice_creates_one_account(self):
        env = credentials()
        self.run_command(env)
        self.run_command(env)

        self.assertEqual(User.objects.filter(username=env[USERNAME_VAR]).count(), 1)
        self.assertEqual(
            PlatformStaff.objects.filter(user__username=env[USERNAME_VAR]).count(), 1
        )
        self.assertEqual(
            PlatformAuditLog.objects.filter(action="platform.admin.provisioned").count(),
            1,
        )

    def test_existing_password_is_not_changed_by_default(self):
        first = credentials()
        self.run_command(first)
        second = dict(first, **{PASSWORD_VAR: strong_password()})

        stdout, _stderr = self.run_command(second)
        user = User.objects.get(username=first[USERNAME_VAR])
        self.assertTrue(user.check_password(first[PASSWORD_VAR]))
        self.assertFalse(user.check_password(second[PASSWORD_VAR]))
        self.assertIn("password was left unchanged", stdout)

    def test_reset_flag_changes_the_password(self):
        first = credentials()
        self.run_command(first)
        replacement = strong_password()
        second = dict(
            first,
            **{PASSWORD_VAR: replacement, RESET_VAR: "true"},
        )

        stdout, _stderr = self.run_command(second)
        user = User.objects.get(username=first[USERNAME_VAR])
        self.assertTrue(user.check_password(replacement))
        self.assertFalse(user.check_password(first[PASSWORD_VAR]))
        self.assertTrue(user.must_change_password)
        self.assertTrue(user.password_set_from_env)
        self.assertIn("password was reset", stdout)

    def test_nothing_is_created_when_variables_are_missing(self):
        env = credentials(**{USERNAME_VAR: "", EMAIL_VAR: "", PASSWORD_VAR: ""})
        stdout, stderr = self.run_command(env)

        self.assertFalse(User.objects.exists())
        self.assertIn("was not created", stdout + stderr)
        self.assertIn(USERNAME_VAR, stdout + stderr)
        self.assertIn(PASSWORD_VAR, stdout + stderr)

    def test_weak_password_is_rejected_without_leaking_it(self):
        weak = "abc123"
        env = credentials(**{PASSWORD_VAR: weak})

        with self.assertRaises(CommandError) as caught:
            self.run_command(env)

        self.assertNotIn(weak, str(caught.exception))
        self.assertFalse(User.objects.exists())

    def test_password_never_appears_in_logs_or_output(self):
        env = credentials()
        password = env[PASSWORD_VAR]

        with self.assertLogs("platform_admin.provisioning", level="DEBUG") as logs:
            stdout, stderr = self.run_command(env)

        self.assertNotIn(password, stdout)
        self.assertNotIn(password, stderr)
        for line in logs.output:
            self.assertNotIn(password, line)

    def test_force_change_can_be_switched_off(self):
        env = credentials(**{FORCE_CHANGE_VAR: "false"})
        self.run_command(env)

        user = User.objects.get(username=env[USERNAME_VAR])
        self.assertFalse(user.must_change_password)
        self.assertTrue(user.password_set_from_env)
