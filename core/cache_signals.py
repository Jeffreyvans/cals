"""Keep the cache honest when something changes.

Signals are deliberately tiny: they only delete keys. If the cache is down or
the backend forgets a value, the next request simply reads the database again.
"""

from __future__ import annotations

from django.apps import apps
from django.db.models.signals import m2m_changed, post_delete, post_save

from core.cache import bump_permission_version, invalidate_dashboard, invalidate_institution


def _institution_id(instance):
    return getattr(instance, "institution_id", None) or getattr(
        instance, "pk", None
    )


def _connect():
    Institution = apps.get_model("core", "Institution")
    InstitutionModule = apps.get_model("core", "InstitutionModule")
    Terminology = apps.get_model("core", "Terminology")
    SchoolSettings = apps.get_model("academics", "SchoolSettings")
    Invoice = apps.get_model("finance", "Invoice")
    Payment = apps.get_model("finance", "Payment")
    Role = apps.get_model("core", "Role")
    RolePermission = apps.get_model("core", "RolePermission")
    User = apps.get_model("accounts", "User")
    Plan = apps.get_model("platform_admin", "Plan")
    Subscription = apps.get_model("platform_admin", "Subscription")

    for model in (Institution, InstitutionModule, Terminology, SchoolSettings):
        post_save.connect(_clear_institution, sender=model, dispatch_uid=f"cache-{model.__name__}")
        post_delete.connect(_clear_institution, sender=model, dispatch_uid=f"cache-del-{model.__name__}")

    for model in (Invoice, Payment):
        post_save.connect(_clear_dashboard, sender=model, dispatch_uid=f"cache-{model.__name__}")
        post_delete.connect(_clear_dashboard, sender=model, dispatch_uid=f"cache-del-{model.__name__}")

    # A plan or subscription change can grant or remove the white-label feature
    # without touching the institution row, so refresh the host list too.
    for model in (Plan, Subscription):
        post_save.connect(_clear_feature_hosts, sender=model, dispatch_uid=f"cache-hosts-{model.__name__}")
        post_delete.connect(_clear_feature_hosts, sender=model, dispatch_uid=f"cache-hosts-del-{model.__name__}")

    for model in (Role, RolePermission, User):
        post_save.connect(_bump_permissions, sender=model, dispatch_uid=f"cache-perm-{model.__name__}")
        post_delete.connect(_bump_permissions, sender=model, dispatch_uid=f"cache-perm-del-{model.__name__}")

    # Adding a role to a user through the many-to-many relation does not save
    # the user row, so listen for that too.
    User.extra_roles.through  # noqa: B018 - existence check
    m2m_changed.connect(
        _bump_permissions, sender=User.extra_roles.through,
        dispatch_uid="cache-perm-extra-roles",
    )


def _clear_institution(sender, instance, **kwargs):
    # An Institution save carries its slug/domain, so clear those cache keys as
    # well. Related rows only carry an institution id.
    if hasattr(instance, "slug") or hasattr(instance, "custom_domain"):
        invalidate_institution(instance)
        from core.hosts import invalidate_dynamic_hosts

        invalidate_dynamic_hosts()
    else:
        invalidate_institution(_institution_id(instance))
        if getattr(instance, "code", None) == "white_label":
            _clear_feature_hosts(sender, instance, **kwargs)


def _clear_feature_hosts(sender, instance, **kwargs):
    """Refresh verified hosts after a plan, subscription or module edit."""
    from core.hosts import invalidate_dynamic_hosts

    invalidate_dynamic_hosts()


def _clear_dashboard(sender, instance, **kwargs):
    invalidate_dashboard(_institution_id(instance))


def _bump_permissions(sender, instance, **kwargs):
    bump_permission_version()


_connected = False


def connect():
    global _connected
    if _connected:
        return
    _connected = True
    _connect()
