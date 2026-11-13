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
    "fiscalisation",
]

# The name each module is given on the screens.
MODULE_LABELS = {
    "core_academics": "Core academics",
    "finance": "Finance and fees",
    "admissions": "Admissions",
    "parent_portal": "Parent portal",
    "lms": "Learning platform",
    "exams": "Examinations",
    "library": "Library",
    "health": "Health and sick bay",
    "counselling": "Counselling",
    "student_affairs": "Student affairs",
    "sports": "Sports and clubs",
    "boarding": "Boarding",
    "transport": "Transport",
    "catering": "Catering and tuck shop",
    "careers_attachment": "Careers and attachment",
    "research": "Research and postgraduate",
    "labs": "Labs and workshops",
    "ict_helpdesk": "ICT helpdesk",
    "hr": "Human resources",
    "payroll": "Payroll",
    "procurement_stores": "Procurement and stores",
    "estates": "Estates and maintenance",
    "security": "Security",
    "compliance": "Compliance and quality",
    "marketing": "Marketing and enquiries",
    "governance": "Governance",
    "api": "API and integrations",
    "sso": "Single sign-on",
    "fiscalisation": "Fiscalisation (ZIMRA FDMS)",
}


def module_label(code):
    """The name of a module for the screens."""
    return MODULE_LABELS.get(code, code.replace("_", " ").capitalize())


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
