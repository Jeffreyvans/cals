"""One shared catalogue of every user-facing error in EduSphere.

Pages, inline messages, JSON responses, logs and the platform Errors page all
read from this list. A message always names its category so a person can tell
whether the problem is their connection, their account or our system.

Each entry carries: a category, code, icon, plain-English title, what happened,
what to do, numbered next steps, and who can fix it.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorSpec:
    code: str
    category: str
    category_label: str
    icon: str
    title: str
    explanation: str
    action: str
    steps: tuple[str, ...]
    fixed_by: str
    default_status: int
    service: str = ""

    @property
    def headline(self):
        """The title with its category and code, safe for any surface."""
        return f"{self.title} ({self.code})"

    @property
    def message(self):
        """A complete one-line message, used by logs and inline alerts."""
        return f"{self.title} ({self.code}). {self.action}"

    def payload(self):
        """The JSON-safe shape shared by API errors and page technical details."""
        return {
            "category": self.category,
            "category_label": self.category_label,
            "code": self.code,
            "icon": self.icon,
            "title": self.title,
            "headline": self.headline,
            "explanation": self.explanation,
            "action": self.action,
            "steps": list(self.steps),
            "fixed_by": self.fixed_by,
        }


def _spec(code, category, label, icon, title, explanation, action, steps, fixed_by, status, service=""):
    return ErrorSpec(
        code=code,
        category=category,
        category_label=label,
        icon=icon,
        title=title,
        explanation=explanation,
        action=action,
        steps=tuple(steps),
        fixed_by=fixed_by,
        default_status=status,
        service=service,
    )


# Category labels are deliberately repeated in every title. Colour and icons
# are helpful, but never the only way to understand the problem.
ERROR_CATALOGUE = (
    # ---- NETWORK ---------------------------------------------------------
    _spec(
        "NET-OFFLINE", "NETWORK", "Network problem", "\u25cf",
        "Network problem: your device looks offline",
        "Your device cannot reach the internet right now, so nothing was sent to EduSphere.",
        "Reconnect to the internet and try again. Your unsaved answers are kept on this device.",
        ("Check your internet or mobile data connection.", "Wait for the connection indicator to return.", "Try again."),
        "user", 503,
    ),
    _spec(
        "NET-SLOW", "NETWORK", "Network problem", "\u25cf",
        "Network problem: your connection is very slow",
        "The request is taking longer than usual because the connection is slow or unstable.",
        "Wait a little longer, or try again on a stronger connection.",
        ("Wait for the page to finish.", "Move closer to your router or switch network.", "Try again."),
        "user", 408,
    ),
    _spec(
        "NET-TIMEOUT", "NETWORK", "Network problem", "\u25cf",
        "Network problem: the request timed out",
        "The request did not reach EduSphere before the timeout. We cannot tell whether it was saved.",
        "Try again with the same request. A retry key prevents duplicate payments or registrations.",
        ("Check your internet connection.", "Wait a few seconds.", "Use Try again - it will not create a duplicate."),
        "user", 408,
    ),
    _spec(
        "NET-UNREACHABLE", "NETWORK", "Network problem", "\u25cf",
        "Network problem: the server could not be reached",
        "Your device could not find or connect to the EduSphere server (DNS or connection failure).",
        "Check your connection and try again. If it persists, ask your administrator to check the school network.",
        ("Check that your internet works in another tab.", "Try again.", "Ask your school administrator if only this network is affected."),
        "user", 503,
    ),
    # ---- SERVER ----------------------------------------------------------
    _spec(
        "SRV-500", "SERVER", "Server problem", "\u25a0",
        "Server problem: our system had a fault, it is not your internet",
        "EduSphere crashed while handling this request. Your device reached us successfully.",
        "Try again. If it keeps happening, send us the reference number below.",
        ("Use Try again once.", "If it repeats, copy the reference number.", "Contact support with the reference number."),
        "developer", 500,
    ),
    _spec(
        "SRV-503", "SERVER", "Server problem", "\u25a0",
        "Server problem: the service is temporarily unavailable",
        "Our service is restarting or briefly unavailable. Your internet connection is fine.",
        "Wait a minute and try again.",
        ("Wait a minute.", "Use Try again.", "Contact support if it lasts more than 15 minutes."),
        "platform admin", 503,
    ),
    _spec(
        "SRV-504", "SERVER", "Server problem", "\u25a0",
        "Server problem: the gateway timed out",
        "A gateway between you and EduSphere waited too long for our server to answer.",
        "Try again in a moment; this is not caused by your device.",
        ("Wait a few seconds.", "Use Try again.", "Contact support with the reference number if it repeats."),
        "platform admin", 504,
    ),
    _spec(
        "SRV-429", "SERVER", "Server problem", "\u25a0",
        "Server problem: the server is too busy",
        "Too many requests were made in a short time. Nothing is broken on your device.",
        "Wait before trying again.",
        ("Wait 30 seconds.", "Avoid refreshing repeatedly.", "Try again."),
        "platform admin", 429,
    ),
    _spec(
        "SRV-MAINT", "SERVER", "Server problem", "\u25a0",
        "Server problem: scheduled maintenance is running",
        "We are upgrading EduSphere right now. Your internet connection is fine.",
        "Wait for maintenance to finish, then try again.",
        ("Check the expected finish time in the notice.", "Wait and try again.", "Contact support if the notice has expired."),
        "platform admin", 503,
    ),
    # ---- DATABASE --------------------------------------------------------
    _spec(
        "DB-DOWN", "DATABASE", "Database problem", "\u25a6",
        "Database problem: the data store is unavailable",
        "EduSphere could not reach its database. Your internet connection worked, but no record could be read or saved.",
        "Try again shortly. If it continues, the platform team must restore the database.",
        ("Wait a few seconds.", "Use Try again.", "Send the reference number to support if it repeats."),
        "platform admin", 503,
    ),
    _spec(
        "DB-CONFLICT", "DATABASE", "Database problem", "\u25a6",
        "Database problem: this record conflicts with another one",
        "The record already exists or was changed by somebody else while you were editing it.",
        "Reload the page, check the latest values, then save again.",
        ("Go back and reload the list.", "Check whether the record now exists.", "Save your change again."),
        "user", 409,
    ),
    _spec(
        "DB-PROTECTED", "DATABASE", "Database problem", "\u25a6",
        "Database problem: this record is in use and cannot be deleted",
        "Other records depend on this one, so deleting it would break your school's history.",
        "Remove the linked records first, or archive this one instead.",
        ("Open the record to see what uses it.", "Ask your school administrator for help.", "Try again after the links are removed."),
        "school administrator", 409,
    ),
    # ---- ACCESS ----------------------------------------------------------
    _spec(
        "AUTH-REQUIRED", "ACCESS", "Access problem", "\u25b3",
        "Access problem: you are not signed in",
        "This page needs an account. We do not know who you are yet.",
        "Sign in with your school or platform account, then try again.",
        ("Sign in with your email or username.", "Use the branded sign-in page for your school.", "Try again after signing in."),
        "user", 403,
    ),
    _spec(
        "AUTH-EXPIRED", "ACCESS", "Access problem", "\u25b3",
        "Access problem: your session expired",
        "You were signed out because your session became too old or ended on another device.",
        "Sign in again. Your place on this page will be kept where possible.",
        ("Sign in again.", "Use Try again to return to this page.", "Contact support if it happens repeatedly."),
        "user", 403,
    ),
    _spec(
        "AUTH-FAILED", "ACCESS", "Access problem", "\u25b3",
        "Access problem: wrong username or password",
        "The details did not match an account. We never reveal whether the username or the password was wrong.",
        "Check your details and try again, or ask an administrator to reset your password.",
        ("Check the email or username exactly.", "Check Caps Lock and your password.", "Ask your school administrator for a reset."),
        "user", 403,
    ),
    _spec(
        "AUTH-LOCKED", "ACCESS", "Access problem", "\u25b3",
        "Access problem: this account is temporarily locked",
        "Too many incorrect sign-in attempts locked the account for a short time.",
        "Wait until the unlock time shown, then sign in again, or ask an administrator to unlock it.",
        ("Wait for the unlock time shown.", "Use the correct password once.", "Ask your school administrator to unlock it early."),
        "school administrator", 423,
    ),
    _spec(
        "AUTH-UNVERIFIED", "ACCESS", "Access problem", "\u25b3",
        "Access problem: your email is not verified",
        "Your account exists, but the email address has not been confirmed yet.",
        "Use the verification link we sent, or ask for a new one.",
        ("Open the verification email.", "Check your spam folder.", "Ask your school administrator to resend it."),
        "user", 403,
    ),
    _spec(
        "AUTH-2FA", "ACCESS", "Access problem", "\u25b3",
        "Access problem: a two-factor code is needed",
        "This action needs a second step from your authenticator app.",
        "Enter the current six-digit code and continue.",
        ("Open your authenticator app.", "Enter the current six-digit code.", "Continue the action."),
        "user", 403,
    ),
    _spec(
        "PERM-DENIED", "ACCESS", "Access problem", "\u25b3",
        "Access problem: you do not have permission",
        "Your account does not have the role needed for this action. The page itself exists.",
        "Ask your school administrator for the role named below, then try again.",
        ("Note the role named below.", "Ask your school administrator to grant it.", "Sign in again if your role was just changed."),
        "school administrator", 403,
    ),
    _spec(
        "ORG-SUSPENDED", "ACCESS", "Access problem", "\u25b3",
        "Access problem: your institution is suspended",
        "Your school's account is suspended, so no one at the school can use EduSphere until it is restored.",
        "Ask your school's billing contact to visit the billing contact page and settle the account.",
        ("Contact your school's billing contact.", "Ask them to open the billing contact page at /billing/contact/.", "Try again after the school is restored."),
        "school administrator", 403,
    ),
    _spec(
        "NOT-FOUND", "ACCESS", "Access problem", "\u25b3",
        "Access problem: that page or record was not found",
        "The address is wrong, or the record was moved or deleted. Your connection and account are fine.",
        "Go back and choose the item again, or check the address.",
        ("Check the web address for a typo.", "Go back to the list and choose the item again.", "Contact support if a link should work."),
        "user", 404,
    ),
    _spec(
        "CSRF", "ACCESS", "Access problem", "\u25b3",
        "Access problem: the security token expired - please reload",
        "The page was open too long or was restored from browser history, so its security token is no longer valid.",
        "Reload the page and submit the form again. No data was sent.",
        ("Reload this page.", "Enter the details again.", "Submit the form once."),
        "user", 403,
    ),
    # ---- INPUT -----------------------------------------------------------
    _spec(
        "INPUT-INVALID", "INPUT", "Input problem", "\u25c7",
        "Input problem: some fields need attention",
        "The server received your answers, but one or more fields are not valid. The page is fine.",
        "Correct the highlighted fields and submit again.",
        ("Read the highlighted field messages.", "Correct those fields.", "Submit the form again."),
        "user", 400,
    ),
    _spec(
        "FILE-REJECTED", "INPUT", "Input problem", "\u25c7",
        "Input problem: the file was rejected",
        "The file you chose is too large, has the wrong type, or is not a valid file. Nothing was uploaded.",
        "Choose a smaller file of an accepted type and try again.",
        ("Check the size and type shown below.", "Choose a different file.", "Upload it again."),
        "user", 400,
    ),
    _spec(
        "IMPORT-ROWS", "INPUT", "Input problem", "\u25c7",
        "Input problem: some spreadsheet rows have errors",
        "The file was read, but one or more rows could not be imported. Valid rows can be imported separately.",
        "Download the row report, fix the listed cells and upload again.",
        ("Open the row error report.", "Fix the listed cells in your spreadsheet.", "Upload the corrected file."),
        "user", 400,
    ),
    # ---- EXTERNAL --------------------------------------------------------
    _spec(
        "EXT-EMAIL", "EXTERNAL", "External service problem", "\u2b21",
        "External service problem: the email service did not respond",
        "Our email provider failed. The action you requested was still completed inside EduSphere.",
        "Use Show one-time link instead, or try sending later.",
        ("Copy the one-time link shown on this page.", "Try again in a few minutes.", "Contact support if emails keep failing."),
        "platform admin", 502, "email",
    ),
    _spec(
        "EXT-PAY", "EXTERNAL", "External service problem", "\u2b21",
        "External service problem: the payment gateway failed",
        "The payment provider did not answer. No money has been taken from your account.",
        "Try again with the same request. A retry key prevents a second charge.",
        ("Check that your payment did not succeed.", "Use Try again with the same retry key.", "Contact support with the reference number."),
        "platform admin", 502, "payment",
    ),
    _spec(
        "EXT-STORAGE", "EXTERNAL", "External service problem", "\u2b21",
        "External service problem: file storage is unavailable",
        "Our file storage service could not be reached. Your internet connection is fine.",
        "Wait a moment and upload again.",
        ("Wait a few seconds.", "Try the upload again.", "Contact support if it keeps failing."),
        "platform admin", 503, "storage",
    ),
    _spec(
        "EXT-FISCAL", "EXTERNAL", "External service problem", "\u2b21",
        "External service problem: the fiscalisation service is unavailable",
        "The tax authority service did not answer, so the document was saved but not submitted.",
        "The submission will retry automatically. You can also retry it from the document page.",
        ("Check the document status later.", "Use Retry submission on the document.", "Contact support if it stays pending."),
        "platform admin", 502, "fiscalisation",
    ),
    _spec(
        "EXT-HOOK", "EXTERNAL", "External service problem", "\u2b21",
        "External service problem: a webhook delivery failed",
        "An outgoing notification to another system failed. EduSphere itself is working.",
        "The delivery will be retried automatically; no action is needed from you.",
        ("Wait for the automatic retry.", "Check the Jobs page if it keeps failing.", "Contact support with the reference number."),
        "developer", 502, "webhook",
    ),
)

ERRORS_BY_CODE = {spec.code: spec for spec in ERROR_CATALOGUE}
CATEGORIES = tuple(dict.fromkeys(spec.category for spec in ERROR_CATALOGUE))
CATEGORY_LABELS = {spec.category: spec.category_label for spec in ERROR_CATALOGUE}


def get_spec(code, default="SRV-500"):
    """Look up a code, falling back to the safest generic server message."""
    return ERRORS_BY_CODE.get(code) or ERRORS_BY_CODE[default]


def code_for_status(status):
    """Status-code fallback used when no exception says more."""
    return {
        400: "INPUT-INVALID",
        401: "AUTH-REQUIRED",
        403: "PERM-DENIED",
        404: "NOT-FOUND",
        405: "INPUT-INVALID",
        408: "NET-TIMEOUT",
        409: "DB-CONFLICT",
        410: "NOT-FOUND",
        412: "CSRF",
        413: "FILE-REJECTED",
        415: "FILE-REJECTED",
        422: "INPUT-INVALID",
        423: "AUTH-LOCKED",
        425: "CSRF",
        429: "SRV-429",
        500: "SRV-500",
        501: "SRV-500",
        502: "SRV-503",
        503: "SRV-503",
        504: "SRV-504",
    }.get(int(status or 500), "SRV-500")


def category_counts(records):
    """Group an iterable of error records (or dicts) by category and code."""
    counts = {}
    for record in records:
        category = getattr(record, "category", None) or (
            record.get("category") if isinstance(record, dict) else None
        )
        code = getattr(record, "code", None) or (
            record.get("code") if isinstance(record, dict) else None
        )
        if category:
            counts[category] = counts.get(category, 0) + 1
        if code:
            counts[code] = counts.get(code, 0) + 1
    return counts


__all__ = [
    "ERROR_CATALOGUE", "ERRORS_BY_CODE", "CATEGORIES", "CATEGORY_LABELS",
    "ErrorSpec", "get_spec", "code_for_status", "category_counts",
]
