"""Startup guard for automatic platform-administrator provisioning.

Calling the management command from ``build.sh`` is the primary path. This
module adds a safe second chance when the web process starts, so a deployment
that was created before the build hook was wired up still gets its admin.

It is deliberately conservative:

* only one run per process,
* only when the ``PLATFORM_ADMIN_*`` variables are present,
* never during tests, migrations, ``collectstatic``, ``check`` or any other
  Django management command that is not starting a server,
* a database that is not ready yet is logged and ignored rather than crashing
  the boot.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_ensured = False

# Management commands that must never touch the database through this hook.
_SKIP_COMMANDS = {
    "test",
    "makemigrations",
    "migrate",
    "collectstatic",
    "check",
    "shell",
    "createsuperuser",
    "changepassword",
    "loaddata",
    "dumpdata",
    "flush",
    "sqlmigrate",
    "showmigrations",
    "ensure_platform_admin",
    "run_finance_jobs",
    "seed",
    "send_test_email",
}

_SERVER_COMMANDS = {"runserver", "runserver_plus", "gunicorn", "uvicorn", "daphne"}


def _management_command():
    """The Django subcommand when this process is ``manage.py <command>``."""
    executable = Path(sys.argv[0]).name.lower()
    if executable.startswith("manage") and len(sys.argv) > 1:
        for argument in sys.argv[1:]:
            if not argument.startswith("-"):
                return argument
    return ""


def should_run():
    if _ensured:
        return False
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    executable = Path(sys.argv[0]).name.lower()
    if "pytest" in executable or "py.test" in executable:
        return False
    if not os.environ.get("PLATFORM_ADMIN_USERNAME"):
        return False

    command = _management_command()
    if command:
        # A management command is only a server start when it explicitly is.
        return command in _SERVER_COMMANDS
    return True


def ensure_once():
    """Run the provisioning service at most once in this process."""
    global _ensured
    if not should_run():
        return None
    _ensured = True
    try:
        from .provisioning import ensure_platform_admin_from_env_or_skip

        return ensure_platform_admin_from_env_or_skip()
    except Exception as exc:  # pragma: no cover - depends on deployment timing
        from django.db import DatabaseError

        if isinstance(exc, DatabaseError):
            logger.warning(
                "Automatic platform administrator check skipped: the database "
                "is not ready yet."
            )
        else:
            logger.exception("Automatic platform administrator check failed")
        return None
