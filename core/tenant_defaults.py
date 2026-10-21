"""Default module switches per institution type (see build plan section 9)."""

PRIMARY = ["core_academics", "finance", "parent_portal", "health", "transport", "library"]

SECONDARY = PRIMARY + [
    "exams",
    "boarding",
    "sports",
    "student_affairs",
    "catering",
    "counselling",
    "hr",
    "payroll",
]

COLLEGE = [
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

VOCATIONAL = [
    "core_academics",
    "finance",
    "admissions",
    "exams",
    "labs",
    "careers_attachment",
    "library",
    "hr",
]

DEFAULT_MODULES = {
    "primary": PRIMARY,
    "secondary": SECONDARY,
    "college": COLLEGE,
    "vocational": VOCATIONAL,
}
