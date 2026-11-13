"""Write an encrypted backup of the financial records.

Zimbabwe's record-keeping rules require financial records to be kept for six
years, so the backup is encrypted at rest with a key held outside the database.

    python manage.py backup_finance --output backups/finance-2026-11-12.json.enc

Set ``EDUSPHERE_FINANCE_BACKUP_KEY`` to a Fernet key first. Generate one with:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Pass ``--institution`` to back up a single institution, or ``--plain`` to write
the JSON without encryption (for a restore into another database).
"""

import json
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers import serialize

from core.models import Institution
from core.tenancy import use_institution

# The models that make up the financial record.
BACKUP_MODELS = [
    "finance.FeeStructure",
    "finance.FeeItem",
    "finance.TaxRate",
    "finance.ExchangeRate",
    "finance.DocumentSequence",
    "finance.Invoice",
    "finance.Payment",
    "finance.Waiver",
    "finance.DebitNote",
    "finance.Account",
    "finance.JournalEntry",
    "finance.JournalLine",
    "finance.Expense",
    "finance.MoneyLog",
    "fiscalisation.FiscalDevice",
    "fiscalisation.FiscalDay",
    "fiscalisation.FiscalSubmission",
]


class Command(BaseCommand):
    help = "Write an encrypted (or plain) backup of the financial records"

    def add_arguments(self, parser):
        parser.add_argument("--output", default=None,
                            help="Where to write the backup file.")
        parser.add_argument("--institution", default=None,
                            help="Slug of one institution; default is all.")
        parser.add_argument("--plain", action="store_true",
                            help="Write JSON without encrypting it.")

    def handle(self, *args, **options):
        institutions = Institution.objects.filter(is_active=True)
        if options["institution"]:
            institutions = institutions.filter(slug=options["institution"])
            if not institutions.exists():
                raise CommandError(
                    f"No institution with slug '{options['institution']}'.")

        payload = {
            "generated": date.today().isoformat(),
            "institutions": {},
        }
        for institution in institutions:
            with use_institution(institution):
                payload["institutions"][institution.slug] = {
                    label: json.loads(serialize("json", _rows(label)))
                    for label in BACKUP_MODELS
                }

        raw = json.dumps(payload).encode("utf-8")
        plain = options["plain"]
        if not plain:
            key = getattr(settings, "FINANCE_BACKUP_KEY", "")
            if not key:
                raise CommandError(
                    "EDUSPHERE_FINANCE_BACKUP_KEY is not set. Generate a key "
                    "with: python -c \"from cryptography.fernet import Fernet; "
                    "print(Fernet.generate_key().decode())\"")
            from cryptography.fernet import Fernet

            raw = Fernet(key.encode("utf-8")).encrypt(raw)

        output = options["output"]
        if output:
            path = Path(output)
            path.parent.mkdir(parents=True, exist_ok=True)
        else:
            suffix = ".json" if plain else ".json.enc"
            path = Path(f"finance-backup-{date.today().isoformat()}{suffix}")
        path.write_bytes(raw)
        self.stdout.write(self.style.SUCCESS(
            f"Backup written to {path} ({len(raw)} bytes)."))


def _rows(label):
    """Fetch the rows for a ``app.Model`` label inside the current tenant."""
    from django.apps import apps

    app_label, model_name = label.split(".")
    model = apps.get_model(app_label, model_name)
    manager = getattr(model, "all_objects", model.objects)
    return manager.all().iterator()
