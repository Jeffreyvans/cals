"""Default roles and their permissions.

Each institution gets its own copy of these roles, so a school can rename a
role or add permissions without touching the code.
"""

from core.permissions import PERMISSION_CODES, PERMISSIONS

ALL = "*"

# These permissions are for the platform, not for a school. A role inside an
# institution never gets them by default, even the institution admin.
PLATFORM_ONLY = {"admin.create_institutions"}

DEFAULT_ROLES = [
    {
        "code": "institution_admin",
        "name": "Institution admin",
        "description": "Runs one institution: users, roles, settings and every module.",
        "permissions": ALL,
    },
    {
        "code": "principal",
        "name": "Principal / Head / Vice-Chancellor",
        "description": "Reads every dashboard and approves the final step in workflows.",
        "permissions": [
            "students.view_all",
            "students.view_own",
            "academics.view_reports",
            "academics.approve_results",
            "academics.release_results",
            "academics.moderate_marks",
            "finance.view_reports",
            "finance.view_ledger",
            "admin.view_audit",
            "admin.workflow_approve",
            "admin.dashboard_school",
            "notices.post",
            "notices.view",
        ],
    },
    {
        "code": "deputy_principal",
        "name": "Deputy principal / Dean",
        "description": "Academic and discipline oversight; approves results.",
        "permissions": [
            "students.view_all",
            "students.view_own",
            "academics.view_reports",
            "academics.approve_results",
            "academics.moderate_marks",
            "admin.workflow_approve",
            "admin.dashboard_school",
            "notices.post",
            "notices.view",
        ],
    },
    {
        "code": "hod",
        "name": "Head of department",
        "description": "Runs staff, courses, timetable slots and moderation in own department.",
        "permissions": [
            "students.view_all",
            "students.view_own",
            "academics.manage_courses",
            "academics.manage_timetable",
            "academics.moderate_marks",
            "academics.view_reports",
            "academics.take_attendance",
            "admin.workflow_approve",
            "admin.manage_departments",
            "notices.post",
            "notices.view",
        ],
    },
    {
        "code": "registrar",
        "name": "Registrar",
        "description": "Admissions, student records, enrolment, transfers and graduation. No money.",
        "permissions": [
            "students.view_all",
            "students.view_own",
            "students.create",
            "students.edit",
            "students.transfer",
            "students.promote",
            "students.graduate",
            "admissions.manage",
            "admissions.review",
            "admissions.decide",
            "academics.manage_structure",
            "academics.manage_courses",
            "academics.manage_timetable",
            "academics.view_reports",
            "exams.manage",
            "admin.manage_departments",
            "notices.post",
            "notices.view",
            "parent_portal.view_child",
        ],
    },
    {
        "code": "exams_officer",
        "name": "Exams officer",
        "description": "Exam timetable, candidate numbers, mark checks, moderation and release.",
        "permissions": [
            "students.view_all",
            "students.view_own",
            "exams.manage",
            "exams.invigilate",
            "academics.approve_results",
            "academics.release_results",
            "academics.moderate_marks",
            "academics.view_reports",
            "notices.view",
        ],
    },
    {
        "code": "bursar",
        "name": "Bursar",
        "description": "All finance: fee structures, invoices, payments, refunds and reports.",
        "permissions": [
            "students.view_all",
            "students.view_own",
            "finance.receive_payment",
            "finance.issue_invoice",
            "finance.approve_refund",
            "finance.manage_fee_structures",
            "finance.view_reports",
            "finance.view_ledger",
            "admin.view_audit",
            "notices.view",
        ],
    },
    {
        "code": "accounts_clerk",
        "name": "Accounts clerk",
        "description": "Receives payments, issues receipts and reads statements.",
        "permissions": [
            "students.view_all",
            "students.view_own",
            "finance.receive_payment",
            "finance.issue_invoice",
            "finance.view_reports",
            "notices.view",
        ],
    },
    {
        "code": "lecturer",
        "name": "Lecturer / Teacher",
        "description": "Own courses and classes: marks, attendance, assignments and comments.",
        "permissions": [
            "students.view_own",
            "academics.enter_marks",
            "academics.take_attendance",
            "academics.view_reports",
            "notices.view",
            "parent_portal.view_child",
        ],
    },
    {
        "code": "class_teacher",
        "name": "Class teacher",
        "description": "Everything a teacher has, plus the class register and parent contact.",
        "permissions": [
            "students.view_own",
            "academics.enter_marks",
            "academics.take_attendance",
            "academics.view_reports",
            "notices.view",
            "parent_portal.view_child",
        ],
    },
    {
        "code": "librarian",
        "name": "Librarian",
        "description": "Library catalogue, loans and fines.",
        "permissions": [
            "students.view_all",
            "library.manage",
            "library.borrow",
            "notices.view",
        ],
    },
    {
        "code": "nurse",
        "name": "Nurse / Health officer",
        "description": "Health records. Restricted and every view is logged.",
        "permissions": [
            "students.view_all",
            "students.view_health",
            "health.view_records",
            "health.manage",
            "notices.view",
        ],
    },
    {
        "code": "counsellor",
        "name": "Counsellor",
        "description": "Counselling notes. Restricted and every view is logged.",
        "permissions": [
            "students.view_all",
            "students.view_counselling",
            "counselling.view_notes",
            "counselling.manage",
            "notices.view",
        ],
    },
    {
        "code": "warden",
        "name": "Warden / Matron",
        "description": "Boarding rooms, roll call and exeat approvals.",
        "permissions": [
            "students.view_all",
            "boarding.manage",
            "notices.view",
        ],
    },
    {
        "code": "transport_officer",
        "name": "Transport officer",
        "description": "Routes, buses, drivers and pick-up lists.",
        "permissions": [
            "students.view_all",
            "transport.manage",
            "notices.view",
        ],
    },
    {
        "code": "hr_officer",
        "name": "HR officer",
        "description": "Staff files, contracts, leave and appraisals.",
        "permissions": [
            "hr.manage",
            "hr.approve_leave",
            "notices.view",
        ],
    },
    {
        "code": "payroll_officer",
        "name": "Payroll officer",
        "description": "Payroll runs and payslips.",
        "permissions": [
            "payroll.manage",
            "notices.view",
        ],
    },
    {
        "code": "procurement_officer",
        "name": "Procurement / Stores officer",
        "description": "Requisitions, purchase orders and stock.",
        "permissions": [
            "procurement.manage",
            "notices.view",
        ],
    },
    {
        "code": "security_officer",
        "name": "Security officer",
        "description": "Visitor log, gate passes and incidents.",
        "permissions": [
            "security.manage",
            "notices.view",
        ],
    },
    {
        "code": "ict_officer",
        "name": "ICT officer",
        "description": "User accounts support, helpdesk tickets and device inventory.",
        "permissions": [
            "ict.manage",
            "notices.view",
        ],
    },
    {
        "code": "marketing_officer",
        "name": "Marketing officer",
        "description": "Enquiries, open days and newsletters.",
        "permissions": [
            "marketing.manage",
            "notices.view",
        ],
    },
    {
        "code": "auditor",
        "name": "Auditor",
        "description": "Read-only access to finance, audit logs and reports.",
        "permissions": [
            "finance.view_reports",
            "finance.view_ledger",
            "admin.view_audit",
            "academics.view_reports",
            "notices.view",
        ],
    },
    {
        "code": "parent",
        "name": "Parent / Guardian",
        "description": "Linked children only: balance, results, attendance and notices.",
        "permissions": [
            "students.view_own",
            "parent_portal.view_child",
            "parent_portal.view_finance",
            "notices.view",
        ],
    },
    {
        "code": "student",
        "name": "Student",
        "description": "Own data only.",
        "permissions": [
            "students.view_own",
            "notices.view",
        ],
    },
]

# Legacy accounts.User.role values map onto these role codes so the older
# screens keep working while the data-driven roles take over.
LEGACY_ROLE_MAP = {
    "admin": "institution_admin",
    "registrar": "registrar",
    "bursar": "bursar",
    "lecturer": "lecturer",
    "student": "student",
    "parent": "parent",
}


def ensure_permissions():
    """Create any missing Permission rows. Returns the number created."""
    from core.models import Permission

    created = 0
    for code, description, category in PERMISSIONS:
        _obj, was_created = Permission.objects.get_or_create(
            code=code, defaults={"description": description, "category": category}
        )
        created += int(was_created)
    return created


def ensure_roles(institution):
    """Create the default roles for an institution if they are missing."""
    from core.models import Permission, Role, RolePermission

    ensure_permissions()
    permissions = {p.code: p for p in Permission.objects.all()}
    roles = {}
    for spec in DEFAULT_ROLES:
        role, _ = Role.objects.get_or_create(
            institution=institution,
            code=spec["code"],
            defaults={"name": spec["name"], "is_system": True, "description": spec["description"]},
        )
        roles[spec["code"]] = role
        if spec["permissions"] == ALL:
            codes = [c for c in PERMISSION_CODES if c not in PLATFORM_ONLY]
        else:
            codes = [c for c in spec["permissions"] if c in permissions]
        for code in codes:
            RolePermission.objects.get_or_create(role=role, permission=permissions[code])
        RolePermission.objects.filter(
            role=role, permission__code__in=PLATFORM_ONLY
        ).delete()
    return roles
