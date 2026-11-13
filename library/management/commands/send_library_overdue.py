"""Daily library job: flag overdue loans and send one reminder a day.

    python manage.py send_library_overdue
    python manage.py send_library_overdue --institution my-school --as-of 2026-11-05
"""

from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import Institution
from core.tenancy import use_institution
from library.services import mark_overdue_loans, send_overdue_notices


class Command(BaseCommand):
    help = "Flag overdue library loans and notify borrowers"

    def add_arguments(self, parser):
        parser.add_argument(
            "--institution", default=None, help="Slug of one institution; default is all."
        )
        parser.add_argument(
            "--as-of", default=None, help="Run as if today were this date (YYYY-MM-DD)."
        )

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
                    f"No active institution with slug '{options['institution']}'."
                )
        if not institutions.exists():
            raise CommandError("No active institution to run for.")

        for institution in institutions:
            with use_institution(institution):
                marked = mark_overdue_loans(as_of)
                sent = send_overdue_notices(as_of)
            self.stdout.write(
                f"{institution.slug}: {marked} flagged overdue, {sent} reminders sent."
            )
