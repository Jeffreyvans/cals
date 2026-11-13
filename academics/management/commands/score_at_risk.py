"""Nightly at-risk scoring.

Run it from cron or a scheduler after the finance job:

    python manage.py score_at_risk

Use ``--institution`` to restrict the run to one institution, ``--as-of`` to
simulate another day, ``--no-notify`` to skip the class-teacher messages and
``--dry-run`` to look without writing.
"""

from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from academics.risk import (
    compute_at_risk,
    notify_class_teachers,
    persist_scores,
)
from core.models import Institution
from core.tenancy import use_institution


class Command(BaseCommand):
    help = "Score students at risk and notify their class teachers"

    def add_arguments(self, parser):
        parser.add_argument("--institution", default=None,
                            help="Slug of one institution; default is all.")
        parser.add_argument("--as-of", default=None,
                            help="Run as if today were this date (YYYY-MM-DD).")
        parser.add_argument("--no-notify", action="store_true",
                            help="Score students without notifying teachers.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Show the result without writing scores.")

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
                rows = compute_at_risk(as_of=as_of)
                written = 0 if options["dry_run"] else len(persist_scores(rows, as_of))
                sent = 0
                if not options["no_notify"] and not options["dry_run"]:
                    sent = notify_class_teachers(rows, as_of)
                self.stdout.write(
                    f"{institution.name}: {len(rows)} students at risk, "
                    f"{written} scores written, {sent} teachers notified.")
