"""Runtime guards for the outbound email configuration.

These Django system checks run on ``manage.py check``, ``runserver`` and every
deployment. They make it impossible to start a production process with a
firewalled SMTP backend or with a provider selected but no credentials.
"""

from django.conf import settings
from django.core.checks import Error, Warning, register

MAILJET_BACKEND = "anymail.backends.mailjet.EmailBackend"
NON_DELIVERING_BACKENDS = {
    "django.core.mail.backends.locmem.EmailBackend",
    "django.core.mail.backends.console.EmailBackend",
    "django.core.mail.backends.dummy.EmailBackend",
}
API_BACKENDS = {
    "anymail.backends.mailjet.EmailBackend",
    "anymail.backends.brevo.EmailBackend",
    "anymail.backends.resend.EmailBackend",
}


@register()
def check_email_backend(app_configs, **kwargs):
    """Block SMTP (and console) backends in production; require provider keys."""
    errors = []
    backend = getattr(settings, "EMAIL_BACKEND", "")
    provider = getattr(settings, "EMAIL_PROVIDER", "")
    debug = getattr(settings, "DEBUG", True)

    if "smtp" in backend.lower():
        errors.append(Error(
            "EMAIL_BACKEND must not use SMTP: Render blocks outbound ports "
            "25, 465 and 587. Use an HTTPS API backend via django-anymail.",
            id="core.email.E001",
        ))

    if not debug:
        if backend not in API_BACKENDS:
            errors.append(Error(
                f"EMAIL_BACKEND must be a supported HTTPS API backend in "
                f"production (got {backend!r}). Set EMAIL_PROVIDER=mailjet "
                f"with MAILJET_API_KEY and MAILJET_SECRET_KEY.",
                id="core.email.E002",
            ))
        if provider == "mailjet" and backend != MAILJET_BACKEND:
            errors.append(Error(
                "EMAIL_PROVIDER=mailjet requires "
                "EMAIL_BACKEND='anymail.backends.mailjet.EmailBackend'.",
                id="core.email.E003",
            ))
        if provider == "mailjet" and not (
            getattr(settings, "MAILJET_API_KEY", "")
            and getattr(settings, "MAILJET_SECRET_KEY", "")
        ):
            errors.append(Error(
                "MAILJET_API_KEY and MAILJET_SECRET_KEY must be set when "
                "EMAIL_PROVIDER=mailjet.",
                id="core.email.E004",
            ))
        if provider == "brevo" and not getattr(settings, "BREVO_API_KEY", ""):
            errors.append(Error(
                "BREVO_API_KEY must be set when EMAIL_PROVIDER=brevo.",
                id="core.email.E005",
            ))
        if provider == "resend" and not getattr(settings, "RESEND_API_KEY", ""):
            errors.append(Error(
                "RESEND_API_KEY must be set when EMAIL_PROVIDER=resend.",
                id="core.email.E006",
            ))
    elif backend in NON_DELIVERING_BACKENDS:
        errors.append(Warning(
            f"The console/locmem email backend ({backend}) is active. "
            "Emails will not be delivered.",
            id="core.email.W001",
        ))
    return errors
