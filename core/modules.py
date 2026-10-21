"""Feature flags: the switchable modules listed in the build plan."""

from core.tenant_defaults import DEFAULT_MODULES

ALL_MODULES = [
    "core_academics",
    "finance",
    "admissions",
    "parent_portal",
    "lms",
    "exams",
    "library",
    "health",
    "counselling",
    "student_affairs",
    "sports",
    "boarding",
    "transport",
    "catering",
    "careers_attachment",
    "research",
    "labs",
    "ict_helpdesk",
    "hr",
    "payroll",
    "procurement_stores",
    "estates",
    "security",
    "compliance",
    "marketing",
    "governance",
    "api",
    "sso",
]


def enabled_modules(institution):
    """The set of module codes switched on for this institution."""
    if institution is None:
        return set()
    rows = list(institution.modules.all())
    if not rows:
        # No switches have been saved yet, so the type defaults apply.
        return set(DEFAULT_MODULES.get(institution.type, []))
    return {row.code for row in rows if row.enabled}


def is_enabled(code, institution=None):
    """True when `code` is switched on for `institution` (current by default)."""
    from core.tenancy import current_institution_id

    if institution is None:
        from core.models import Institution

        institution = Institution.objects.filter(pk=current_institution_id()).first()
    if institution is None:
        return False
    return code in enabled_modules(institution)


def set_module(institution, code, enabled):
    from core.models import InstitutionModule

    obj, _ = InstitutionModule.objects.get_or_create(institution=institution, code=code)
    obj.enabled = enabled
    obj.save(update_fields=["enabled"])
    return obj


def apply_defaults(institution):
    """Switch on the default modules for the institution's type."""
    from core.models import InstitutionModule

    codes = DEFAULT_MODULES.get(institution.type, [])
    for code in codes:
        InstitutionModule.objects.get_or_create(
            institution=institution, code=code, defaults={"enabled": True}
        )
    return codes
