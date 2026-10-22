from django.core.management.base import BaseCommand, CommandError

from academics.demo import DEMO_PASSWORD, seed_demo
from core.models import Institution


class Command(BaseCommand):
    help = "Create demo users, academic structure, students, fees and timetables"

    def add_arguments(self, parser):
        parser.add_argument("--mode", choices=["school", "college"], default=None,
            help="Switch EduSphere to school mode or college mode after seeding.")
        parser.add_argument("--institution", default=None,
            help="Slug of the institution to seed. Defaults to the default one.")
        parser.add_argument("--prefix", default="",
            help="Put this in front of every demo username, so a second "
                 "institution can have its own 'admin'.")

    def handle(self, *a, **o):
        slug = o.get("institution") or "default"
        institution = Institution.objects.filter(slug=slug).first()
        if institution is None:
            raise CommandError(f"No institution with slug '{slug}'.")

        prefix = o.get("prefix") or ""
        seed_demo(institution, prefix=prefix, mode=o.get("mode"))
        self.stdout.write(self.style.SUCCESS(
            f"Seeded demo data for {institution.name}. "
            f"Logins (password {DEMO_PASSWORD}): "
            f"{', '.join(prefix + name for name in ('admin', 'registrar', 'bursar', 'lecturer', 'parent', 'student1-5'))}"))
