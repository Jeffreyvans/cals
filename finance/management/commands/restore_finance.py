"""Restore a financial backup written by :mod:`backup_finance`.

Restores are deliberately conservative:

* ``--dry-run`` is the default and reports each institution and model count.
* ``--confirm`` is required before anything is written.
* Rows whose primary key already exists are skipped, so a restore can be
  retried safely; nothing is overwritten or deleted.
* Institutions are matched by slug and all ``institution`` foreign keys are
  remapped to the target primary key. Institutions and users are **not**
  created by this command: provision them first (with the same user ids when
  restoring into a different database).

    python manage.py restore_finance --input backup.json.enc --dry-run
    python manage.py restore_finance --input backup.json.enc --confirm
"""

import json
from pathlib import Path

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers import deserialize
from django.db import transaction

from core.models import Institution
from core.tenancy import use_institution
from finance.management.commands.backup_finance import BACKUP_MODELS, FORMAT_VERSION


def _read_payload(path, key):
    raw = path.read_bytes()
    if raw.startswith(b"gAAAAA"):
        if not key:
            raise CommandError(
                "This backup is encrypted. Set EDUSPHERE_FINANCE_BACKUP_KEY "
                "before restoring it."
            )
        from cryptography.fernet import Fernet, InvalidToken

        try:
            raw = Fernet(key.encode("utf-8")).decrypt(raw)
        except InvalidToken as exc:
            raise CommandError("The backup key is wrong or the file is damaged.") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise CommandError("The backup file is not valid JSON.") from exc
    if not isinstance(payload, dict) or "institutions" not in payload:
        raise CommandError("The backup file is not a Vanpere financial backup.")
    version = payload.get("format_version", 1)
    if version not in (1, FORMAT_VERSION):
        raise CommandError(f"Unsupported backup format version: {version}.")
    return payload


def _snapshot(section):
    """Accept both the version 1 flat layout and the version 2 counted layout."""
    if "models" in section:
        return section["models"]
    return section


def _target_institutions(payload, requested_slug):
    slugs = sorted(payload["institutions"])
    if requested_slug:
        if requested_slug not in slugs:
            raise CommandError(f"The backup has no institution '{requested_slug}'.")
        slugs = [requested_slug]
    missing = [slug for slug in slugs if not Institution.objects.filter(slug=slug).exists()]
    if missing:
        raise CommandError(
            "Create these institutions before restoring (slug, name and user ids "
            f"must match): {', '.join(missing)}"
        )
    return [Institution.objects.get(slug=slug) for slug in slugs]


class Command(BaseCommand):
    help = "Restore a financial backup written by backup_finance (dry run by default)"

    def add_arguments(self, parser):
        parser.add_argument("--input", required=True, help="Backup file to read.")
        parser.add_argument("--institution", default=None,
                            help="Restore only this institution slug.")
        parser.add_argument("--confirm", action="store_true",
                            help="Write rows. Without it, only reports.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Explicitly report what would be restored (default).")

    def handle(self, *args, **options):
        path = Path(options["input"])
        if not path.is_file():
            raise CommandError(f"Backup file not found: {path}")
        from django.conf import settings

        payload = _read_payload(path, getattr(settings, "FINANCE_BACKUP_KEY", ""))
        institutions = _target_institutions(payload, options["institution"])
        confirm = options["confirm"]
        created_total = skipped_total = 0

        for institution in institutions:
            snapshot = _snapshot(payload["institutions"][institution.slug])
            with use_institution(institution), transaction.atomic():
                created = skipped = 0
                for label in BACKUP_MODELS:
                    rows = snapshot.get(label, [])
                    model = apps.get_model(label)
                    for row in rows:
                        if model.all_objects.filter(pk=row.get("pk")).exists():
                            skipped += 1
                            continue
                        # Institution primary keys can differ between databases.
                        if "institution" in row.get("fields", {}):
                            row["fields"]["institution"] = institution.pk
                        objects = deserialize(
                            "json", json.dumps([row]), ignorenonexistent=True,
                            handle_forward_references=True,
                        )
                        for obj in objects:
                            if not confirm:
                                created += 1
                                continue
                            obj.save(using=None)
                            obj.save_deferred_fields()
                            created += 1
                if not confirm:
                    # A dry run must not write anything, including audit rows.
                    transaction.set_rollback(True)
                created_total += created
                skipped_total += skipped
                verb = "restored" if confirm else "would restore"
                self.stdout.write(
                    f"{institution.name}: {verb} {created} rows, "
                    f"skipping {skipped} rows that already exist."
                )

        if not confirm:
            self.stdout.write(self.style.WARNING(
                "Dry run only. Re-run with --confirm to write these rows."))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"Restored {created_total} rows; skipped {skipped_total} existing rows."))
