"""Measure the things that decide whether 10,000 users fit.

Run it against a copy of production data::

    python manage.py measure_performance
    python manage.py measure_performance --username registrar --institution alpha

It prints:

* row counts for the large tables, so an index can be judged against real data;
* the indexes PostgreSQL/SQLite actually has on those tables;
* query count, response time and uncompressed page size for the main screens;
* the cache backend, ``CONN_MAX_AGE`` and static-file compression settings.

Nothing is written to the database. The page measurements only run when an
active superuser (or ``--username``) can sign in.
"""

import time
from contextlib import ExitStack

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import NoReverseMatch, reverse

from core.models import Institution

TABLES = [
    "accounts.User",
    "academics.Student",
    "admissions.Application",
    "finance.Invoice",
    "finance.Payment",
    "core.AuditEntry",
    "core.Notification",
    "core.OutboundMessage",
    "core.ImportBatch",
    "core.BackgroundJob",
]

PAGES = [
    ("Dashboard", "dashboard", {}),
    ("Student search", "student_search", {}),
    ("Admissions list", "admissions:application_list", {}),
    ("Staff list", "staff_list", {}),
    ("Debtors report", "finance:report_debtors", {}),
    ("Global search", "global_search", {"q": "a"}),
]


def _rows(model):
    manager = getattr(model, "all_objects", model._default_manager)
    try:
        return manager.count()
    except Exception:  # pragma: no cover - a table that does not exist yet
        return 0


def _table_name(model):
    return model._meta.db_table


class Command(BaseCommand):
    help = "Report row counts, indexes, query counts and page sizes."

    def add_arguments(self, parser):
        parser.add_argument("--institution", default="",
                            help="Institution slug for the page measurements.")
        parser.add_argument("--username", default="",
                            help="User to sign in as (default: first active superuser).")
        parser.add_argument("--no-pages", action="store_true",
                            help="Only report tables, indexes and settings.")

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("EduSphere performance snapshot"))
        self._settings()
        self._tables()
        if not options["no_pages"]:
            self._pages(options["institution"], options["username"])
        self.stdout.write(self.style.SUCCESS("Done. Record the numbers in PROGRESS.md."))

    # ---------- settings ----------
    def _settings(self):
        cache_backend = settings.CACHES["default"]["BACKEND"].rsplit(".", 1)[-1]
        database = settings.DATABASES["default"]
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_LABEL("Configuration"))
        self.stdout.write(f"  database          {database['ENGINE'].rsplit('.', 1)[-1]}")
        self.stdout.write(f"  CONN_MAX_AGE      {database.get('CONN_MAX_AGE', 0)}s")
        self.stdout.write(f"  cache             {cache_backend}")
        self.stdout.write(f"  celery broker     {'configured' if settings.CELERY_BROKER_URL else 'inline (no broker)'}")
        self.stdout.write(f"  storage           {settings.STORAGES['default']['BACKEND'].rsplit('.', 1)[-1]}")
        self.stdout.write(f"  static            {settings.STORAGES['staticfiles']['BACKEND'].rsplit('.', 1)[-1]}")

    # ---------- tables and indexes ----------
    def _tables(self):
        from django.apps import apps

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_LABEL("Large tables"))
        self.stdout.write(f"  {'table':32} {'rows':>10}  {'indexes':>7}")
        for label in TABLES:
            try:
                model = apps.get_model(label)
            except LookupError:  # pragma: no cover - optional app
                continue
            table = _table_name(model)
            indexes = self._indexes(table)
            self.stdout.write(f"  {table:32} {_rows(model):>10}  {len(indexes):>7}")
            if indexes:
                self.stdout.write(f"      {', '.join(indexes)}")

    def _indexes(self, table):
        try:
            with connection.cursor() as cursor:
                constraints = connection.introspection.get_constraints(cursor, table)
        except Exception:  # pragma: no cover - a table that does not exist
            return []
        names = []
        for name, info in constraints.items():
            if info.get("index"):
                names.append(name)
        return sorted(names)

    # ---------- pages ----------
    def _pages(self, institution_slug, username):
        from accounts.models import User

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_LABEL("Pages (uncompressed, test client)"))
        user = None
        if username:
            user = User.objects.filter(username=username, is_active=True).first()
        if user is None:
            user = User.objects.filter(is_active=True, is_superuser=True).order_by("pk").first()
        if user is None:
            self.stdout.write("  No active superuser; pass --username to measure pages.")
            return

        client = Client()
        client.force_login(user)
        institution = None
        if institution_slug:
            institution = Institution.objects.filter(slug=institution_slug).first()
        headers = {"HTTP_HOST": "localhost"}
        if institution is not None:
            headers["HTTP_X_INSTITUTION_SLUG"] = institution.slug

        self.stdout.write(f"  signed in as {user.username}")
        self.stdout.write(f"  {'page':24} {'status':>6} {'queries':>7} {'ms':>7} {'KB':>7}")
        for label, name, params in PAGES:
            try:
                url = reverse(name, kwargs=params) if name in (
                    "student_detail", "invoice_detail") else reverse(name)
            except NoReverseMatch:
                self.stdout.write(f"  {label:24} {'--':>6} (no URL)")
                continue
            if params and name == "global_search":
                url = f"{url}?q={params['q']}"
            with ExitStack() as stack:
                queries = stack.enter_context(CaptureQueriesContext(connection))
                started = time.perf_counter()
                response = client.get(url, **headers)
                elapsed = (time.perf_counter() - started) * 1000
            size_kb = len(response.content) / 1024
            self.stdout.write(
                f"  {label:24} {response.status_code:>6} {len(queries):>7} "
                f"{elapsed:>7.0f} {size_kb:>7.1f}")
        self.stdout.write(
            "  Aim: under 25 queries and under 300 KB for a public/first screen.")
