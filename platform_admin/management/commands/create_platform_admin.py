"""Create or update a platform staff account from the command line.

Platform rights never come from Django's superuser flag. This command links an
ordinary user to an explicit :class:`~platform_admin.models.PlatformStaff` row,
which is the only thing the platform screens look at.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Institution
from platform_admin.models import PlatformRole, PlatformStaff
from platform_admin.services import ensure_platform_roles

User = get_user_model()


class Command(BaseCommand):
    help = "Create or update a platform administrator or platform staff member."

    def add_arguments(self, parser):
        parser.add_argument("email", help="Email address of the platform staff member.")
        parser.add_argument("--first-name", default="")
        parser.add_argument("--last-name", default="")
        parser.add_argument(
            "--role",
            default="main_admin",
            help="Platform role code: main_admin, support, billing or compliance.",
        )
        parser.add_argument(
            "--main-admin",
            action="store_true",
            help="Give this person full control of the platform.",
        )
        parser.add_argument(
            "--password",
            default="",
            help="Password to set. Omit to leave the account without a usable password.",
        )
        parser.add_argument(
            "--inactive",
            action="store_true",
            help="Create the platform row switched off.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        email = (options["email"] or "").strip().lower()
        if "@" not in email:
            raise CommandError("Give a valid email address.")
        roles = ensure_platform_roles()
        role = roles.get(options["role"])
        if role is None:
            role = PlatformRole.objects.filter(code=options["role"]).first()
        if role is None:
            raise CommandError(
                f"Unknown platform role {options['role']!r}. "
                f"Choose one of: {', '.join(sorted(roles))}."
            )

        institution = (
            Institution.objects.filter(slug="default").first()
            or Institution.objects.order_by("pk").first()
        )
        if institution is None:
            raise CommandError("Create an institution before adding platform staff.")

        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            username_seed = email.split("@")[0][:40] or "platform"
            username = username_seed
            counter = 1
            while User.objects.filter(username__iexact=username).exists():
                counter += 1
                username = f"{username_seed[:34]}{counter}"
            user = User(
                username=username,
                email=email,
                first_name=options["first_name"][:150],
                last_name=options["last_name"][:150],
                role=User.Role.STAFF,
                is_staff=False,
                is_active=True,
                institution=institution,
            )
            user.set_unusable_password()
            user.save()
        else:
            changed = False
            if options["first_name"] and user.first_name != options["first_name"]:
                user.first_name = options["first_name"][:150]
                changed = True
            if options["last_name"] and user.last_name != options["last_name"]:
                user.last_name = options["last_name"][:150]
                changed = True
            if changed:
                user.save(update_fields=["first_name", "last_name"])

        if options["password"]:
            user.set_password(options["password"])
            user.must_change_password = False
            user.save(update_fields=["password", "must_change_password"])

        staff, created = PlatformStaff.objects.update_or_create(
            user=user,
            defaults={
                "role": role,
                "is_main_admin": bool(options["main_admin"]),
                "is_active": not options["inactive"],
            },
        )
        verb = "Created" if created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} platform staff {user.email} as {role.name} "
                f"({'main administrator' if staff.is_main_admin else 'staff'})."
            )
        )
