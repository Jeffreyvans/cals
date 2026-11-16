"""Permissions held by platform staff.

Platform permissions are deliberately separate from the institution permission
catalogue. An institution administrator is trusted with their own school, never
with the platform, so none of the codes here ever enter an institution role.
"""

ALL = "*"

PLATFORM_PERMISSIONS = [
    # code, description, category
    ("platform.view_institutions", "See institutions, plans and operating counts", "institutions"),
    ("platform.manage_institutions", "Create, edit, suspend, archive and delete institutions", "institutions"),
    ("platform.manage_modules", "Switch modules on or off per institution", "institutions"),
    ("platform.manage_admins", "Manage institution administrator accounts", "institutions"),
    ("platform.manage_plans", "Manage plans and subscriptions", "billing"),
    ("platform.manage_billing", "Manage invoices and payments", "billing"),
    ("platform.view_applications", "Read institution applications", "applications"),
    ("platform.manage_applications", "Review, approve and reject institution applications", "applications"),
    ("platform.view_inbox", "See the platform decision inbox", "applications"),
    ("platform.manage_staff", "Manage platform staff and their roles", "staff"),
    ("platform.view_audit", "Read the platform audit log", "audit"),
    ("platform.manage_settings", "Change platform settings and legal pages", "settings"),
    ("platform.send_announcements", "Send platform announcements", "settings"),
    ("platform.view_health", "See platform health, usage and error summaries", "operations"),
    ("platform.manage_backups", "Manage backups", "operations"),
    ("platform.request_access", "Ask an institution for consent to see protected data", "access"),
    ("platform.emergency_access", "Ask for emergency break-glass access", "access"),
    ("platform.approve_emergency", "Approve another platform admin's emergency request", "access"),
]

PLATFORM_PERMISSION_CODES = [code for code, _desc, _cat in PLATFORM_PERMISSIONS]
PLATFORM_PERMISSION_LOOKUP = {
    code: {"description": description, "category": category}
    for code, description, category in PLATFORM_PERMISSIONS
}

SUPPORT_PERMISSIONS = [
    "platform.view_institutions",
    "platform.view_applications",
    "platform.manage_applications",
    "platform.view_inbox",
    "platform.view_health",
    "platform.request_access",
    "platform.emergency_access",
]

BILLING_PERMISSIONS = [
    "platform.view_institutions",
    "platform.manage_plans",
    "platform.manage_billing",
    "platform.view_inbox",
    "platform.request_access",
]

COMPLIANCE_PERMISSIONS = [
    "platform.view_institutions",
    "platform.view_applications",
    "platform.view_inbox",
    "platform.view_audit",
    "platform.request_access",
    "platform.emergency_access",
    "platform.approve_emergency",
]

PLATFORM_ROLE_PRESETS = [
    {
        "code": "main_admin",
        "name": "Platform main administrator",
        "description": "Full control of the platform. Still needs consent for institution data.",
        "permissions": ALL,
    },
    {
        "code": "support",
        "name": "Support",
        "description": "Applications, operational counts and consented support access.",
        "permissions": SUPPORT_PERMISSIONS,
    },
    {
        "code": "billing",
        "name": "Billing",
        "description": "Plans, subscriptions, invoices and payments.",
        "permissions": BILLING_PERMISSIONS,
    },
    {
        "code": "compliance",
        "name": "Compliance",
        "description": "Audit, applications and emergency approvals.",
        "permissions": COMPLIANCE_PERMISSIONS,
    },
]


def has_code(staff, code):
    """True when a PlatformStaff row may use a platform permission."""
    if staff is None or not getattr(staff, "is_active", False):
        return False
    if staff.is_main_admin:
        return True
    role = getattr(staff, "role", None)
    if role is None:
        return False
    held = role.permissions or []
    return ALL in held or code in held
