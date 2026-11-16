"""Create or repair the main platform administrator from the environment.

Run this after ``migrate`` on every deployment (``build.sh`` does it). The
command is idempotent, reads every value from environment variables and never
prints or stores the password in plain text.

Environment variables:

* ``PLATFORM_ADMIN_USERNAME`` (required)
* ``PLATFORM_ADMIN_EMAIL`` (required)
* ``PLATFORM_ADMIN_PASSWORD`` (required)
* ``PLATFORM_ADMIN_RESET_PASSWORD`` (optional; ``true`` replaces the password)
* ``PLATFORM_ADMIN_FORCE_CHANGE`` (optional; defaults to ``true``)
"""

from django.core.management.base import BaseCommand, CommandError

from platform_admin.provisioning import (
    PASSWORD_VAR,
    ensure_platform_admin_from_env,
)


class Command(BaseCommand):
    help = "Create or repair the main platform administrator from environment variables."

    def add_arguments(self, parser):
        parser.add_argument(
            "--allow-failure",
            action="store_true",
            help="Report an invalid password but exit successfully.",
        )

    def handle(self, *args, **options):
        result = ensure_platform_admin_from_env(via="build")
        for message in result.messages:
            stream = self.stderr if not result.ok else self.stdout
            style = self.style.SUCCESS if result.ok else self.style.ERROR
            stream.write(style(message))

        if result.status == "invalid" and not options["allow_failure"]:
            # Fail loudly but never echo the password: the messages come from
            # Django's validators and contain no secret material.
            raise CommandError(
                f"The password supplied in {PASSWORD_VAR} was rejected. "
                "No account was changed."
            )
        return None
