"""Audit trail helpers.

Every change to money, results, users and roles is written here. Health and
counselling records get their own log so a restricted view can never go
unnoticed.
"""

import json

from core.models import AuditEntry, SensitiveViewLog


def _client_ip(request):
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def log_action(actor, action, instance=None, before=None, after=None, request=None, **extra):
    """Write one audit row. Safe to call from anywhere."""
    from core.tenancy import current_institution_id

    institution = extra.pop("institution", None)
    institution_id = (
        institution.pk if institution is not None else getattr(actor, "institution_id", None)
    ) or current_institution_id()
    model_name = extra.pop("model_name", "")
    object_id = extra.pop("object_id", "")
    if instance is not None:
        model_name = model_name or instance.__class__.__name__
        object_id = object_id or str(instance.pk)
    entry = AuditEntry(
        institution_id=institution_id,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        model_name=model_name,
        object_id=object_id,
        before=_jsonify(before),
        after=_jsonify(after),
        ip_address=_client_ip(request),
    )
    entry.save()
    _mirror_to_platform(
        entry,
        action,
        institution_id,
        model_name,
        object_id,
        before,
        after,
        request,
    )
    return entry


def _mirror_to_platform(entry, action, institution_id, model_name, object_id,
                        before, after, request):
    """Copy a platform-staff change into the tamper-evident platform log.

    Institution screen changes write the ordinary institution audit row above.
    When the person making the change is EduSphere platform staff, the same
    change is also written to :class:`platform_admin.models.PlatformAuditLog`
    with its before and after values and the consent grant in force. Importing
    at call time keeps the module import order simple and avoids a cycle.
    """
    actor = entry.actor
    if actor is None:
        return
    try:
        from platform_admin.services import (
            active_grant_for,
            is_platform_staff,
            log_platform_action,
        )
    except Exception:  # pragma: no cover - platform app not installed
        return
    if not is_platform_staff(actor):
        return
    institution = None
    if institution_id:
        from core.models import Institution

        institution = Institution.objects.filter(pk=institution_id).first()
    grant = active_grant_for(actor, institution) if institution is not None else None
    mirror_action = (
        action
        if action.startswith(("platform.", "access.", "application.", "institution."))
        else f"institution.change.{action}"
    )
    log_platform_action(
        actor,
        mirror_action,
        institution=institution,
        grant=grant,
        record_type=model_name,
        record_id=object_id,
        before=before,
        after=after,
        request=request,
    )


def _jsonify(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    return json.loads(json.dumps(value, default=str))


def log_permission_denied(request, codes):
    """Record a refused attempt so an administrator can review it."""
    from core.tenancy import current_institution_id

    AuditEntry.objects.create(
        institution_id=current_institution_id(),
        actor=request.user if request.user.is_authenticated else None,
        action="permission.denied",
        model_name="view",
        object_id=request.path[:180],
        after={"codes": list(codes)},
        ip_address=_client_ip(request),
    )


def log_sensitive_view(request, record_type, record_id, view_name=""):
    """Record that somebody opened a restricted record."""
    from core.tenancy import current_institution_id

    return SensitiveViewLog.objects.create(
        institution_id=current_institution_id(),
        actor=request.user if request.user.is_authenticated else None,
        record_type=record_type,
        record_id=str(record_id),
        view_name=view_name or request.path[:180],
        ip_address=_client_ip(request),
    )
