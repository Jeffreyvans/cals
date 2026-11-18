"""Seed platform roles and plans once the database exists."""

from django.contrib.auth.signals import user_logged_in
from django.db.models.signals import post_migrate
from django.dispatch import receiver


@receiver(post_migrate)
def seed_platform_defaults(sender, **kwargs):
    if getattr(sender, "name", "") != "platform_admin":
        return
    from .services import ensure_default_plans, ensure_platform_roles

    ensure_platform_roles()
    ensure_default_plans()


@receiver(user_logged_in)
def note_first_admin_sign_in(sender, request, user, **kwargs):
    """Mark the onboarding checklist's "administrator signed in" step."""
    if getattr(user, "is_platform_admin", False):
        return
    from .onboarding import mark_admin_signed_in

    mark_admin_signed_in(user, request)
