"""Daily finance job: late penalties, overdue reminders and the message queue.

Run it from cron or a scheduler:

    python manage.py run_finance_jobs

Use ``--institution`` to restrict the run to one institution, ``--as-of`` to
simulate another day, and ``--no-penalties`` / ``--no-reminders`` / ``--no-send``
to turn parts off.
"""

from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import Institution
from core.tenancy import use_institution
from finance import services
from finance.models import LatePenaltyRule


class Command(BaseCommand):
    help = "Apply late penalties and queue overdue-fee reminders"

    def add_arguments(self, parser):
        parser.add_argument("--institution", default=None,
                            help="Slug of one institution; default is all.")
        parser.add_argument("--as-of", default=None,
                            help="Run as if today were this date (YYYY-MM-DD).")
        parser.add_argument("--no-penalties", action="store_true")
        parser.add_argument("--no-reminders", action="store_true")
        parser.add_argument("--no-send", action="store_true",
                            help="Queue messages but do not deliver them.")

    def handle(self, *args, **options):
        as_of = timezone.localdate()
        if options["as_of"]:
            try:
                as_of = date.fromisoformat(options["as_of"])
            except ValueError as exc:
                raise CommandError("--as-of must be YYYY-MM-DD.") from exc

        institutions = Institution.objects.filter(is_active=True)
        if options["institution"]:
            institutions = institutions.filter(slug=options["institution"])
            if not institutions.exists():
                raise CommandError(
                    f"No active institution with slug '{options['institution']}'.")
        if not institutions.exists():
            raise CommandError("No active institution to run for.")

        for institution in institutions:
            with use_institution(institution):
                penalties = 0
                if not options["no_penalties"]:
                    for rule in LatePenaltyRule.objects.filter(is_active=True):
                        penalties += len(services.apply_late_penalties(
                            rule, as_of=as_of))
                reminders = {"students": 0, "notifications": 0, "messages": 0}
                if not options["no_reminders"]:
                    reminders = services.send_overdue_reminders(
                        as_of=as_of, send=not options["no_send"])
                elif not options["no_send"]:
                    from core.notifications import send_pending

                    send_pending()
                self.stdout.write(
                    f"{institution.name}: {penalties} penalties, "
                    f"{reminders['students']} students reminded, "
                    f"{reminders['messages']} messages queued "
                    f"({reminders['notifications']} in-app).")
