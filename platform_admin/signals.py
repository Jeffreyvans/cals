"""Seed platform roles and plans once the database exists."""

from django.db.models.signals import post_migrate
from django.dispatch import receiver


@receiver(post_migrate)
def seed_platform_defaults(sender, **kwargs):
    if getattr(sender, "name", "") != "platform_admin":
        return
    from .services import ensure_default_plans, ensure_platform_roles

    ensure_platform_roles()
    ensure_default_plans()
