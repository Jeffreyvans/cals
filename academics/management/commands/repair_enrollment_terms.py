"""Attach a real term to enrolments that came across from the old text field.

The 0011 migration already matched every old term name it could. An enrolment
could still end up without a term when the institution had no term at all at
the time, or when the old label did not name one. This command gives those
rows the current term, or the earliest term of their institution.
"""

from django.core.management.base import BaseCommand

from academics.models import Enrollment, Term


class Command(BaseCommand):
    help = "Give enrolments with no term a sensible real term."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would change without writing anything.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        terms = {}
        fixed = 0
        orphaned = 0

        for enrollment in Enrollment.all_objects.filter(term__isnull=True).order_by("pk"):
            institution_id = enrollment.institution_id
            if institution_id not in terms:
                options_for_institution = Term.all_objects.filter(
                    institution_id=institution_id)
                terms[institution_id] = (
                    options_for_institution.filter(is_current=True).first()
                    or options_for_institution.order_by("start_date", "pk").first())
            term = terms[institution_id]
            if term is None:
                orphaned += 1
                continue
            if not dry_run:
                Enrollment.all_objects.filter(pk=enrollment.pk).update(term=term)
            fixed += 1
            self.stdout.write(
                f"Enrolment {enrollment.pk}: {enrollment.label} -> {term}")

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run: nothing was saved."))
        self.stdout.write(self.style.SUCCESS(
            f"{fixed} enrolment(s) repaired; {orphaned} left without a term "
            "because their institution has no term."))
