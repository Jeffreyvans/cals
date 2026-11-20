"""Backup, restore and retention tests.

A backup that cannot be restored is not a backup, and a retention command that
deletes another tenant's rows is worse than no command at all.
"""

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from cryptography.fernet import Fernet
from django.apps import apps
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from core.models import Institution
from finance.management.commands.backup_finance import BACKUP_MODELS
from finance.models import MoneyLog, TaxCategory, TaxRate


class BackupCoverageTests(TestCase):
    def test_backup_lists_every_finance_and_fiscalisation_model(self):
        finance = {
            f"finance.{model.__name__}"
            for model in apps.get_app_config("finance").get_models()
        }
        fiscal = {
            f"fiscalisation.{model.__name__}"
            for model in apps.get_app_config("fiscalisation").get_models()
        }
        self.assertEqual(set(BACKUP_MODELS), finance | fiscal)


class RestoreRoundTripTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.institution = Institution.objects.get(slug="default")

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "finance-backup.json"
        TaxRate.objects.create(
            name="Standard", category=TaxCategory.STANDARD,
            rate=Decimal("15.5"), effective_from=date(2026, 1, 1),
            institution=self.institution,
        )

    def test_plain_backup_can_be_restored_and_is_idempotent(self):
        call_command("backup_finance", "--output", str(self.path), "--plain")
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(payload["format_version"], 2)
        self.assertEqual(
            payload["institutions"][self.institution.slug]["counts"]["finance.TaxRate"], 1)

        original_pk = TaxRate.objects.get().pk
        TaxRate.all_objects.all().delete()
        self.assertFalse(TaxRate.objects.exists())

        call_command("restore_finance", "--input", str(self.path), "--dry-run")
        self.assertFalse(TaxRate.objects.exists(), "a dry run must not write rows")

        call_command("restore_finance", "--input", str(self.path), "--confirm")
        restored = TaxRate.objects.get()
        self.assertEqual(restored.pk, original_pk)
        self.assertEqual(restored.rate, Decimal("15.5"))
        self.assertEqual(restored.institution_id, self.institution.pk)

        call_command("restore_finance", "--input", str(self.path), "--confirm")
        self.assertEqual(TaxRate.objects.count(), 1)

    def test_encrypted_backup_requires_the_right_key(self):
        key = Fernet.generate_key().decode()
        with override_settings(FINANCE_BACKUP_KEY=key):
            call_command("backup_finance", "--output", str(self.path))
            TaxRate.all_objects.all().delete()
            with override_settings(FINANCE_BACKUP_KEY=Fernet.generate_key().decode()):
                with self.assertRaises(CommandError):
                    call_command("restore_finance", "--input", str(self.path), "--confirm")
            call_command("restore_finance", "--input", str(self.path), "--confirm")
        self.assertEqual(TaxRate.objects.count(), 1)


class PruneTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.first = Institution.objects.get(slug="default")
        cls.second = Institution.objects.create(
            name="Prune Second", slug="prune-second", type="college")

    def test_old_money_logs_are_removed_for_every_institution(self):
        old = date.today() - timedelta(days=8 * 365)
        for institution in (self.first, self.second):
            row = MoneyLog.all_objects.create(
                institution=institution, action=MoneyLog.Action.INVOICE,
                amount=Decimal("10.00"), description="Old entry")
            MoneyLog.all_objects.filter(pk=row.pk).update(created=old)
            recent = MoneyLog.all_objects.create(
                institution=institution, action=MoneyLog.Action.INVOICE,
                amount=Decimal("20.00"), description="Recent entry")
            MoneyLog.all_objects.filter(pk=recent.pk).update(created=date.today())

        call_command("prune_finance", "--before", "2019-01-01", "--confirm")
        self.assertEqual(MoneyLog.all_objects.count(), 2)
        for institution in (self.first, self.second):
            rows = MoneyLog.all_objects.filter(institution=institution)
            self.assertEqual(rows.count(), 1)
            self.assertEqual(rows.get().amount, Decimal("20.00"))

    def test_prune_refuses_dates_inside_the_retention_window(self):
        with self.assertRaises(CommandError):
            call_command("prune_finance", "--before", "2025-01-01")
