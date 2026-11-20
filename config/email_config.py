"""Pure provider selection for the platform's outbound email.

Kept separate from ``settings.py`` so the choice can be tested without
reloading Django settings. All providers use django-anymail's HTTPS API, so no
code in this project ever opens an SMTP connection (Render blocks ports 25,
465 and 587).
"""

# Backend names from the current django-anymail documentation.
BACKENDS = {
    "mailjet": "anymail.backends.mailjet.EmailBackend",
    "resend": "anymail.backends.resend.EmailBackend",
    "brevo": "anymail.backends.brevo.EmailBackend",
    "console": "django.core.mail.backends.console.EmailBackend",
    "locmem": "django.core.mail.backends.locmem.EmailBackend",
    "filebased": "django.core.mail.backends.filebased.EmailBackend",
}

PROVIDERS = frozenset(BACKENDS)


def first(env, *names, default=""):
    """First non-blank value from a mapping of environment variables."""
    for name in names:
        value = env.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def select_email_settings(env, *, timeout=10, debug=True):
    """Return the email settings implied by ``env``.

    ``env`` can be ``os.environ`` or a plain dict. The returned dict always
    contains EMAIL_PROVIDER, EMAIL_BACKEND, ANYMAIL, the individual API keys
    and a list of human-readable ``warnings`` for the startup log.
    """
    warnings = []
    provider = first(env, "EMAIL_PROVIDER", "EDUSPHERE_EMAIL_PROVIDER").lower()
    resend_key = first(env, "RESEND_API_KEY", "EDUSPHERE_RESEND_API_KEY")
    mailjet_key = first(env, "MAILJET_API_KEY", "EDUSPHERE_MAILJET_API_KEY")
    mailjet_secret = first(env, "MAILJET_SECRET_KEY", "EDUSPHERE_MAILJET_SECRET_KEY")
    brevo_key = first(env, "BREVO_API_KEY", "EDUSPHERE_BREVO_API_KEY")

    if not provider:
        # Backwards compatibility: an existing Resend-only deployment keeps
        # working without an explicit EMAIL_PROVIDER. Otherwise console wins.
        provider = "resend" if resend_key else "console"
    if provider not in PROVIDERS:
        warnings.append(
            f"Unknown EMAIL_PROVIDER={provider!r}; falling back to the console backend."
        )
        provider = "console"

    # Anymail's documented key is uppercase REQUESTS_TIMEOUT. A connection and
    # read timeout of 10 seconds keeps a slow provider from freezing a page.
    anymail = {"REQUESTS_TIMEOUT": timeout}

    if provider == "mailjet":
        mailjet_api_url = first(
            env, "MAILJET_API_URL", "EDUSPHERE_MAILJET_API_URL",
            default="https://api.mailjet.com/v3.1/",
        )
        anymail.update({
            "MAILJET_API_KEY": mailjet_key,
            "MAILJET_SECRET_KEY": mailjet_secret,
            "MAILJET_API_URL": mailjet_api_url,
        })
        if not (mailjet_key and mailjet_secret):
            warnings.append(
                "EMAIL_PROVIDER=mailjet but MAILJET_API_KEY and/or "
                "MAILJET_SECRET_KEY are missing; Mailjet will reject every send."
            )
    elif provider == "resend":
        anymail.update({
            "RESEND_API_KEY": resend_key,
            "RESEND_API_URL": first(
                env, "RESEND_API_URL", "EDUSPHERE_RESEND_API_URL",
                default="https://api.resend.com/",
            ),
        })
        if not resend_key:
            warnings.append("EMAIL_PROVIDER=resend but RESEND_API_KEY is missing.")
    elif provider == "brevo":
        anymail.update({
            "BREVO_API_KEY": brevo_key,
            "BREVO_API_URL": first(
                env, "BREVO_API_URL", "EDUSPHERE_BREVO_API_URL",
                default="https://api.brevo.com/v3/",
            ),
        })
        if not brevo_key:
            warnings.append("EMAIL_PROVIDER=brevo but BREVO_API_KEY is missing.")
    elif not debug:
        warnings.append(
            "EMAIL_PROVIDER=console in production: emails are printed to the "
            "console, not delivered. Set EMAIL_PROVIDER=mailjet."
        )

    if not debug and "smtp" in BACKENDS[provider].lower():
        # Belt and braces: production must never use a blocked SMTP port.
        raise ValueError("SMTP email backends are not allowed in production.")

    return {
        "EMAIL_PROVIDER": provider,
        "EMAIL_BACKEND": BACKENDS[provider],
        "ANYMAIL": anymail,
        "RESEND_API_KEY": resend_key,
        "MAILJET_API_KEY": mailjet_key,
        "MAILJET_SECRET_KEY": mailjet_secret,
        "BREVO_API_KEY": brevo_key,
        "MAILJET_API_URL": first(
            env, "MAILJET_API_URL", "EDUSPHERE_MAILJET_API_URL",
            default="https://api.mailjet.com/v3.1/",
        ),
        "RESEND_API_URL": first(
            env, "RESEND_API_URL", "EDUSPHERE_RESEND_API_URL",
            default="https://api.resend.com/",
        ),
        "BREVO_API_URL": first(
            env, "BREVO_API_URL", "EDUSPHERE_BREVO_API_URL",
            default="https://api.brevo.com/v3/",
        ),
        "warnings": warnings,
    }
