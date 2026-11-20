"""Check and enforce the six-year retention rule for financial records.

Issued financial documents can never be edited or deleted through the
application. This command is the one controlled way to remove records that are
*older than six years*, which the law allows. It refuses to touch anything
inside the retention window.

    python manage.py prune_finance --before 2019-01-01        # dry run
    python manage.py prune_finance --before 2019-01-01 --confirm

The ``--before`` date must be at least six years ago. Without ``--confirm`` the
command only reports what it would remove.
"""

from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Institution
from core.tenancy import use_institution
from finance.models import DebitNote, Invoice, MoneyLog, Payment, Waiver

RETENTION_YEARS = 6

# (label, model, date field). The date field decides what falls outside the
# retention window.
PRUNABLE = [
    ("credit notes", Waiver, "created"),
    ("debit notes", DebitNote, "created"),
    ("payments", Payment, "paid_on"),
    ("invoices", Invoice, "created"),
]


class Command(BaseCommand):
    help = "Remove financial records older than the six-year retention period"

    def add_arguments(self, parser):
        parser.add_argument("--before", required=True,
                            help="Remove records created before this date "
                                 "(YYYY-MM-DD). Must be at least six years ago.")
        parser.add_argument("--confirm", action="store_true",
                            help="Actually delete. Without it, only reports.")
        parser.add_argument("--institution", default=None,
                            help="Slug of one institution; default is all.")

    def handle(self, *args, **options):
        try:
            before = date.fromisoformat(options["before"])
        except ValueError as exc:
            raise CommandError("--before must be a date like 2019-01-01.") from exc
        cutoff = date(date.today().year - RETENTION_YEARS, 1, 1)
        if before > cutoff:
            raise CommandError(
                f"Records from {before} are inside the six-year retention "
                f"window (which ends {cutoff}). Financial records cannot be "
                "removed before then.")

        institutions = Institution.objects.all()
        if options["institution"]:
            institutions = institutions.filter(slug=options["institution"])
            if not institutions.exists():
                raise CommandError(
                    f"No institution with slug '{options['institution']}'.")

        total = 0
        for institution in institutions:
            with use_institution(institution):
                for label, model, date_field in PRUNABLE:
                    rows = model.all_objects.filter(
                        **{f"{date_field}__lt": before})
                    count = rows.count()
                    if not count:
                        continue
                    total += count
                    if options["confirm"]:
                        with transaction.atomic():
                            rows.delete()
                        self.stdout.write(
                            f"{institution.name}: removed {count} {label}.")
                    else:
                        self.stdout.write(
                            f"{institution.name}: would remove {count} {label}.")
                # Only touch this institution's audit rows. The old code
                # deleted every institution's rows on the first pass.
                logs = MoneyLog.all_objects.filter(
                    institution=institution, created__lt=before)
                log_count = logs.count()
                if log_count:
                    total += log_count
                    if options["confirm"]:
                        with transaction.atomic():
                            logs.delete()
                        verb = "removed"
                    else:
                        verb = "would remove"
                    self.stdout.write(
                        f"{institution.name}: {verb} {log_count} audit entries.")
        if not total:
            self.stdout.write("Nothing older than the cutoff was found.")
        elif not options["confirm"]:
            self.stdout.write(self.style.WARNING(
                "Dry run only. Re-run with --confirm to remove these records."))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"Removed {total} records older than {before}."))
