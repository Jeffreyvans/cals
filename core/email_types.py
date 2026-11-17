"""Canonical email types used by the OutboundMessage log.

Every message the platform sends carries one of these codes. The codes are the
single source of truth for the model choices, the Email health page, the
``check_email`` command, the tests and the audit table in DEPLOY.md. Adding a
new type means adding it here and passing it to :func:`core.notifications.send_email`.
"""

# General / one-off
GENERAL = "general"
OTHER = "other"
EMAIL_CHECK = "email_check"

# Identity and access
EMAIL_VERIFICATION = "email_verification"
RESEND_VERIFICATION = "resend_verification"
PASSWORD_RESET_REQUEST = "password_reset_request"
PASSWORD_RESET_CONFIRMATION = "password_reset_confirmation"
PASSWORD_CHANGE_NOTICE = "password_change_notice"
FORCED_PASSWORD_CHANGE_NOTICE = "forced_password_change_notice"
STAFF_INVITATION = "staff_invitation"
STUDENT_INVITATION = "student_invitation"
SET_PASSWORD_LINK = "set_password_link"
SIGNUP_LINK = "signup_link"
REGISTRATION_REQUEST_DECISION = "registration_request_decision"
SECURITY_ALERT = "security_alert"

# Institution applications and platform access
STUDENT_APPLICATION_RECEIVED = "student_application_received"
INSTITUTION_APPLICATION_RECEIVED = "institution_application_received"
INSTITUTION_APPLICATION_APPROVED = "institution_application_approved"
INSTITUTION_APPLICATION_MORE_INFO = "institution_application_more_info"
INSTITUTION_APPLICATION_REJECTED = "institution_application_rejected"
PLATFORM_ACCESS_REQUEST = "platform_access_request"
PLATFORM_ACCESS_DECISION = "platform_access_decision"

# Billing, fees and finance
BILLING_REMINDER = "billing_reminder"
SUSPENSION_WARNING = "suspension_warning"
FEE_REMINDER = "fee_reminder"
FEE_RECEIPT = "fee_receipt"

# Teaching and learning
RESULTS_NOTIFICATION = "results_notification"
ABSENCE_NOTIFICATION = "absence_notification"
LIBRARY_OVERDUE = "library_overdue"

# Public forms, announcements and operations
CONTACT_FORM = "contact_form"
DEMO_REQUEST = "demo_request"
ANNOUNCEMENT = "announcement"
WELCOME = "welcome"
ACCOUNT_CREATED = "account_created"
ROLE_CHANGED = "role_changed"

# Operations and administrators
ADMIN_ERROR = "admin_error"

# (code, human readable label) — ordered like the DEPLOY.md audit table.
EMAIL_TYPES = (
    (GENERAL, "General"),
    (EMAIL_VERIFICATION, "Email verification"),
    (RESEND_VERIFICATION, "Resend verification"),
    (PASSWORD_RESET_REQUEST, "Password reset request"),
    (PASSWORD_RESET_CONFIRMATION, "Password reset confirmation notice"),
    (PASSWORD_CHANGE_NOTICE, "Voluntary password change notice"),
    (FORCED_PASSWORD_CHANGE_NOTICE, "Forced password change notice"),
    (STAFF_INVITATION, "Staff invitation"),
    (STUDENT_INVITATION, "Student invitation"),
    (SET_PASSWORD_LINK, "Set-password link"),
    (SIGNUP_LINK, "Sign-up link"),
    (REGISTRATION_REQUEST_DECISION, "Registration request decision"),
    (STUDENT_APPLICATION_RECEIVED, "Student application received"),
    (INSTITUTION_APPLICATION_RECEIVED, "Institution application received"),
    (INSTITUTION_APPLICATION_APPROVED, "Institution application approved"),
    (INSTITUTION_APPLICATION_MORE_INFO, "Institution application more information"),
    (INSTITUTION_APPLICATION_REJECTED, "Institution application rejected"),
    (PLATFORM_ACCESS_REQUEST, "Platform access request"),
    (PLATFORM_ACCESS_DECISION, "Platform access decision"),
    (BILLING_REMINDER, "Billing reminder"),
    (SUSPENSION_WARNING, "Suspension warning"),
    (FEE_REMINDER, "Fee reminder"),
    (FEE_RECEIPT, "Fee receipt"),
    (RESULTS_NOTIFICATION, "Results notification"),
    (ABSENCE_NOTIFICATION, "Absence notification"),
    (LIBRARY_OVERDUE, "Library overdue notice"),
    (CONTACT_FORM, "Contact form message"),
    (DEMO_REQUEST, "Demo request message"),
    (ANNOUNCEMENT, "Announcement / newsletter"),
    (SECURITY_ALERT, "2FA / security alert"),
    (ADMIN_ERROR, "Error email to administrators"),
    (WELCOME, "Welcome message"),
    (ACCOUNT_CREATED, "Account created notice"),
    (ROLE_CHANGED, "Role changed notice"),
    (EMAIL_CHECK, "check_email test message"),
    (OTHER, "Other"),
)

EMAIL_TYPE_CODES = frozenset(code for code, _label in EMAIL_TYPES)
EMAIL_TYPE_LABELS = dict(EMAIL_TYPES)
