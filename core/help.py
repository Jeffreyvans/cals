"""Role-aware help content, shared by the in-app help page and the docs.

Each guide lists the tasks a role performs most often and the URL name of the
screen that performs them. Missing URL names are skipped safely, so a guide
never breaks when a module is disabled.
"""

PLATFORM = "platform"
ADMIN = "admin"
REGISTRAR = "registrar"
FINANCE = "finance"
TEACHING = "teaching"
STUDENT = "student"
PARENT = "parent"
GENERAL = "general"

GUIDES = {
    PLATFORM: {
        "title": "Platform administrator",
        "intro": (
            "You manage institutions, plans and platform-wide operations. "
            "You never see institution student, health or counselling records unless "
            "a consent grant explicitly allows it."
        ),
        "tasks": [
            ("Review new institution applications", "platform_admin:applications"),
            ("Create and configure an institution", "platform_admin:institutions"),
            ("Manage plans and subscriptions", "platform_admin:plans"),
            ("Record platform payments", "platform_admin:invoices"),
            ("Review consent requests", "platform_admin:access_requests"),
            ("Check errors and background jobs", "platform_admin:errors"),
            ("Review the platform audit log", "platform_admin:audit_log"),
            ("Keep your account secure", "platform_admin:security"),
        ],
        "tips": [
            "Use the staging institution for training before onboarding a real school.",
            "Check the System status page before diagnosing reported outages.",
            "Every action is written to the tamper-evident platform audit log.",
        ],
    },
    ADMIN: {
        "title": "Institution administrator",
        "intro": (
            "You configure the school, manage users and roles, and control which "
            "modules are enabled. You are the first point of contact for data access."
        ),
        "tasks": [
            ("Set up the institution and branding", "institution_settings"),
            ("Add the current academic year and term", "academic_years"),
            ("Manage users and roles", "user_list"),
            ("Review registration requests", "registration_requests"),
            ("Configure numbering and grade bands", "numbering_settings"),
            ("Review data access requests", "platform_admin:institution_access_requests"),
            ("Read the audit trail", "audit_trail"),
        ],
        "tips": [
            "Set the current year and term before registering students or recording results.",
            "Bulk import is the fastest way to add students; always dry-run with a small file first.",
            "Enable only the modules the school will actually use.",
        ],
    },
    REGISTRAR: {
        "title": "Registrar",
        "intro": (
            "You own student records, admissions, registration, timetables and results. "
            "You are the custodian of academic data quality."
        ),
        "tasks": [
            ("Find a student", "student_search"),
            ("Register a student", "student_create"),
            ("Bulk import students", "import_start"),
            ("Review applications and offers", "admissions:application_list"),
            ("Manage years, terms and classes", "academic_years"),
            ("Build the timetable", "timetable"),
            ("Post notices", "notices"),
        ],
        "tips": [
            "Check duplicate student numbers before approving an import.",
            "Student numbers are normalised for comparison but stored exactly as entered.",
            "Results are only visible to the student and their guardians once published.",
        ],
    },
    FINANCE: {
        "title": "Bursar / Accounts",
        "intro": (
            "You manage fee structures, invoices, payments, refunds and financial "
            "reports. Every money movement is written to the ledger."
        ),
        "tasks": [
            ("Review debtors", "report_debtors"),
            ("Review collections", "report_collections"),
            ("Create fee structures", "fee_structures"),
            ("Generate invoices", "generate_invoices"),
            ("Reconcile the bank", "bank_lines"),
            ("Review refunds", "refund_list"),
            ("Read the trial balance", "trial_balance"),
        ],
        "tips": [
            "Always record the payment reference; it is the audit link to the bank statement.",
            "Refunds cannot exceed the original payment, including pending requests.",
            "Use the money log and audit trail to investigate a disputed balance.",
        ],
    },
    TEACHING: {
        "title": "Teacher / Lecturer",
        "intro": (
            "You manage your courses, assignments, attendance and marks. You only "
            "see the students you teach."
        ),
        "tasks": [
            ("Open my courses", "lecturer_portal"),
            ("View my timetable", "my_timetable"),
            ("Set assignments", "lecturer_assignments"),
            ("Browse the learning modules", "lms:course_list"),
            ("Post notices", "notices"),
            ("View teacher-safe health alerts", "health:safe_alerts"),
        ],
        "tips": [
            "Marks are private to the student and their guardians.",
            "Health alerts are deliberately limited; full records require a separate grant.",
            "If a student is missing from your course, ask the registrar to check enrolment.",
        ],
    },
    STUDENT: {
        "title": "Student",
        "intro": (
            "Your workspace shows your registration, courses, results and fees. "
            "Nobody else can see your personal or health information."
        ),
        "tasks": [
            ("Open my account", "student_portal"),
            ("Register for courses", "my_registration"),
            ("Open learning materials", "lms:course_list"),
            ("Change my password", "password_change"),
        ],
        "tips": [
            "Check your profile details at the start of each term.",
            "Never share your login or one-time codes with anyone.",
            "If a fee or result looks wrong, contact the school office with the reference shown on screen.",
        ],
    },
    PARENT: {
        "title": "Parent / Guardian",
        "intro": (
            "You can see your children's academic progress and fees. Access is "
            "limited to learners explicitly linked to your account."
        ),
        "tasks": [
            ("View my children", "parent_portal"),
            ("Open learning materials", "lms:course_list"),
            ("Change my password", "password_change"),
        ],
        "tips": [
            "Only children linked to your account appear in the portal.",
            "You cannot see another learner's records, even if you know their student number.",
            "Contact the school office to update a guardian link.",
        ],
    },
    GENERAL: {
        "title": "Staff member",
        "intro": (
            "Your workspace is shaped by the permissions attached to your role. "
            "Use this page to find the most common screens and to keep your account safe."
        ),
        "tasks": [
            ("Open the overview", "dashboard"),
            ("Browse school services", "module_directory"),
            ("Open my department", "department_portal"),
            ("Search the school", "global_search"),
        ],
        "tips": [
            "If a menu item is missing, your role does not have that permission; ask an administrator.",
            "Report a suspicious message or login immediately to the school office.",
            "The System status page shows whether a service is actually available.",
        ],
    },
}


def guide_for(user):
    """Pick the most useful guide for a user, without raising on odd roles."""
    if getattr(user, "is_platform_admin", False):
        return PLATFORM
    role = (getattr(user, "role", "") or "").lower()
    if role == "admin":
        return ADMIN
    if role in {"registrar", "admissions"}:
        return REGISTRAR
    if role in {"bursar", "accountant", "finance"}:
        return FINANCE
    if role in {"lecturer", "teacher", "hod", "dean"}:
        return TEACHING
    if role == "student":
        return STUDENT
    if role == "parent":
        return PARENT
    return GENERAL
