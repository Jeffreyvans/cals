import os
import secrets
import sys
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

from .env import env_bool, env_int, env_list, env_str, load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _first(*names, default=""):
    """The first environment variable that is set to a non-blank value.

    The generic names are the ones documented for Render (``SECRET_KEY``,
    ``DATABASE_URL`` and so on). The older ``EDUSPHERE_`` names stay as
    fallbacks so an existing local ``.env`` or systemd unit keeps working.
    """
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value
    return default


RENDER_EXTERNAL_HOSTNAME = _first("RENDER_EXTERNAL_HOSTNAME").strip()
RENDER = env_bool("RENDER", bool(RENDER_EXTERNAL_HOSTNAME))

# Local development works with no variables at all. Render sets RENDER, so a
# production process is safe by default; render.yaml also sets DEBUG=False.
DEBUG = env_bool("DEBUG", env_bool("EDUSPHERE_DEBUG", not RENDER))

SECRET_KEY = _first("SECRET_KEY", "EDUSPHERE_SECRET_KEY").strip()
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = secrets.token_urlsafe(50)   # throwaway dev key
    else:
        raise ImproperlyConfigured(
            "SECRET_KEY (or EDUSPHERE_SECRET_KEY) must be set when DEBUG is "
            "off. Generate one with: "
            "python -c \"import secrets; print(secrets.token_urlsafe(50))\"")

ALLOWED_HOSTS = env_list(
    "ALLOWED_HOSTS", env_list("EDUSPHERE_ALLOWED_HOSTS", ["*"] if DEBUG else [])
)
if RENDER_EXTERNAL_HOSTNAME and RENDER_EXTERNAL_HOSTNAME not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)

if not DEBUG and not ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        "ALLOWED_HOSTS (or EDUSPHERE_ALLOWED_HOSTS) must list your domain(s) "
        "when DEBUG is off, e.g. ALLOWED_HOSTS=fees.school.org")

CSRF_TRUSTED_ORIGINS = env_list(
    "CSRF_TRUSTED_ORIGINS", env_list("EDUSPHERE_CSRF_TRUSTED_ORIGINS", [])
)
if RENDER_EXTERNAL_HOSTNAME:
    render_origin = f"https://{RENDER_EXTERNAL_HOSTNAME}"
    if render_origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(render_origin)

# HTTPS and proxy handling. Render terminates TLS in front of the app, so the
# forwarded-proto header tells Django the request really arrived over HTTPS.
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_REFERRER_POLICY = "same-origin"
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"
elif env_bool("EDUSPHERE_SSL", False):
    # Backwards-compatible opt-in for a local machine behind a TLS proxy.
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = env_int("EDUSPHERE_HSTS_SECONDS", 0)

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

TESTING = (
    "test" in sys.argv
    or "pytest" in sys.modules
    or "PYTEST_CURRENT_TEST" in os.environ
)
if TESTING:
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "django.contrib.sitemaps",
    "accounts", "academics", "finance", "admissions", "lms",
    "operations",
    "library", "health", "counselling", "student_affairs", "sports",
    "boarding", "transport", "catering", "careers", "examinations",
    "research", "labs", "ict_helpdesk",
    "core",
    "platform_admin",
    "fiscalisation",
    "rest_framework", "drf_spectacular", "api",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.middleware.ForcePasswordChangeMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "core.middleware.InstitutionMiddleware",
    "platform_admin.middleware.PlatformDataAccessMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"], "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "academics.context.institution",
        "platform_admin.context.platform_context",
    ]},
}]
# --- database --------------------------------------------------------------
# DATABASE_URL is what Render (and Supabase, Neon and friends) provide. With no
# DATABASE_URL the project falls back to the bundled SQLite file, so a local
# checkout still runs with no configuration.
DATABASE_URL = _first("DATABASE_URL", "EDUSPHERE_DATABASE_URL").strip()
if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL, conn_max_age=600, conn_health_checks=True
        )
    }
    if DATABASES["default"]["ENGINE"].endswith("postgresql"):
        options = DATABASES["default"].setdefault("OPTIONS", {})
        sslmode = _first("DB_SSL_MODE", default="require" if not DEBUG else "").strip()
        if sslmode:
            options.setdefault("sslmode", sslmode)
else:
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / env_str("EDUSPHERE_DB_PATH", "db.sqlite3")}}
AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"   # target of collectstatic
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / env_str("EDUSPHERE_MEDIA_PATH", "media")

# --- static and uploaded files --------------------------------------------
# WhiteNoise serves the collected, hashed static files. Uploaded files use the
# local filesystem in development and any S3-compatible bucket in production
# (Supabase Storage, Cloudflare R2 or Backblaze B2 all speak the same API).
STORAGE_BACKEND = _first(
    "STORAGE_BACKEND", "EDUSPHERE_STORAGE_BACKEND", default="local"
).strip().lower()
USE_S3_STORAGE = STORAGE_BACKEND in {"s3", "r2", "b2", "supabase", "object"}

# The hashed manifest only exists after ``collectstatic``. Production (and the
# Render build) runs it, so production uses the compressed manifest storage.
# Local development and the test suite use the plain storage: ``runserver``
# serves the source files directly and tests do not depend on a build artefact.
STATICFILES_BACKEND = (
    "django.contrib.staticfiles.storage.StaticFilesStorage"
    if DEBUG or TESTING
    else "whitenoise.storage.CompressedManifestStaticFilesStorage"
)

if USE_S3_STORAGE:
    AWS_ACCESS_KEY_ID = _first("AWS_ACCESS_KEY_ID", "EDUSPHERE_S3_ACCESS_KEY").strip()
    AWS_SECRET_ACCESS_KEY = _first(
        "AWS_SECRET_ACCESS_KEY", "EDUSPHERE_S3_SECRET_KEY"
    ).strip()
    AWS_STORAGE_BUCKET_NAME = _first(
        "AWS_STORAGE_BUCKET_NAME", "EDUSPHERE_S3_BUCKET"
    ).strip()
    AWS_S3_ENDPOINT_URL = _first(
        "AWS_S3_ENDPOINT_URL", "EDUSPHERE_S3_ENDPOINT_URL"
    ).strip()
    AWS_S3_REGION_NAME = _first(
        "AWS_S3_REGION_NAME", "EDUSPHERE_S3_REGION", default="auto"
    ).strip()
    AWS_S3_CUSTOM_DOMAIN = _first(
        "AWS_S3_CUSTOM_DOMAIN", "EDUSPHERE_S3_CUSTOM_DOMAIN"
    ).strip()
    AWS_S3_FILE_OVERWRITE = False
    AWS_DEFAULT_ACL = None
    AWS_QUERYSTRING_AUTH = True
    AWS_S3_SIGNATURE_VERSION = "s3v4"
    AWS_LOCATION = _first("AWS_LOCATION", "EDUSPHERE_S3_PREFIX").strip()

    _s3_options = {
        "bucket_name": AWS_STORAGE_BUCKET_NAME,
        "region_name": AWS_S3_REGION_NAME,
        "default_acl": None,
        "file_overwrite": False,
        "querystring_auth": True,
        "signature_version": AWS_S3_SIGNATURE_VERSION,
    }
    if AWS_S3_ENDPOINT_URL:
        _s3_options["endpoint_url"] = AWS_S3_ENDPOINT_URL
    if AWS_S3_CUSTOM_DOMAIN:
        _s3_options["custom_domain"] = AWS_S3_CUSTOM_DOMAIN
    if AWS_LOCATION:
        _s3_options["location"] = AWS_LOCATION
    STORAGES = {
        "default": {"BACKEND": "storages.backends.s3.S3Storage", "OPTIONS": _s3_options},
        "staticfiles": {"BACKEND": STATICFILES_BACKEND},
    }
else:
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": STATICFILES_BACKEND},
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
TIME_ZONE = env_str("EDUSPHERE_TIME_ZONE", "Africa/Harare")
USE_TZ = True
INSTITUTION_NAME = env_str("EDUSPHERE_INSTITUTION_NAME",
                           "Lindenet Institute of Technology")   # change per school
CURRENCY = env_str("EDUSPHERE_CURRENCY", "$")

# --- email ----------------------------------------------------------------
# Render blocks outbound SMTP on ports 25, 465 and 587. Every message goes
# through Django's email backend: Anymail's Resend HTTPS API when a key is
# configured, and the console backend otherwise (local development). Django's
# test runner swaps in the in-memory backend automatically, so mail.outbox
# keeps working in tests. There are deliberately no EMAIL_HOST/PORT/TLS
# settings: nothing in this project can open an SMTP connection.
RESEND_API_KEY = _first("RESEND_API_KEY", "EDUSPHERE_RESEND_API_KEY").strip()
EMAIL_FROM_DOMAIN = _first(
    "EMAIL_FROM_DOMAIN", "EDUSPHERE_EMAIL_FROM_DOMAIN",
    default="edusphere.local",
).strip()
DEFAULT_FROM_EMAIL = _first(
    "DEFAULT_FROM_EMAIL", "EDUSPHERE_EMAIL_FROM",
    default=f"EduSphere <no-reply@{EMAIL_FROM_DOMAIN}>",
).strip()
SERVER_EMAIL = _first(
    "SERVER_EMAIL", "EDUSPHERE_SERVER_EMAIL", default=DEFAULT_FROM_EMAIL,
).strip()
EMAIL_FROM = DEFAULT_FROM_EMAIL   # backwards-compatible alias used by old code

if RESEND_API_KEY:
    EMAIL_BACKEND = "anymail.backends.resend.EmailBackend"
    ANYMAIL = {
        "RESEND_API_KEY": RESEND_API_KEY,
        "RESEND_API_URL": _first(
            "RESEND_API_URL", "EDUSPHERE_RESEND_API_URL",
            default="https://api.resend.com/",
        ).strip(),
        # Anymail's requests timeout; keep it short so a slow provider cannot
        # hold a request (or a worker) open.
        "requests_timeout": env_int("EMAIL_TIMEOUT", 10),
    }
else:
    EMAIL_BACKEND = _first(
        "EMAIL_BACKEND", "EDUSPHERE_EMAIL_BACKEND",
        default="django.core.mail.backends.console.EmailBackend",
    )

# A slow provider must never freeze a request.
EMAIL_TIMEOUT = env_int("EMAIL_TIMEOUT", 10)
RESEND_WEBHOOK_SECRET = _first(
    "RESEND_WEBHOOK_SECRET", "EDUSPHERE_RESEND_WEBHOOK_SECRET",
).strip()

# Links in emails must be built from the public base URL, never from the
# request host (a request may arrive on Render's internal hostname).
SITE_URL = _first(
    "SITE_URL", "EDUSPHERE_SITE_URL",
    default=(
        f"https://{RENDER_EXTERNAL_HOSTNAME}"
        if RENDER_EXTERNAL_HOSTNAME else "http://localhost:8000"
    ),
).strip().rstrip("/")
if not DEBUG and SITE_URL.startswith("http://"):
    SITE_URL = "https://" + SITE_URL[len("http://"):]

# Queue retries: five attempts with exponential backoff. Opt in to Celery
# dispatch with EMAIL_ASYNC=true on a deployment that actually runs a worker.
EMAIL_ASYNC = env_bool("EMAIL_ASYNC", False)
EMAIL_MAX_RETRIES = env_int("EMAIL_MAX_RETRIES", 5)
EMAIL_RETRY_BACKOFF_SECONDS = env_int("EMAIL_RETRY_BACKOFF_SECONDS", 60)

# Abuse protection. Verification and reset are limited per address and per IP;
# bulk mail is capped per institution so one tenant cannot use the quota.
EMAIL_VERIFY_LIMIT = env_int("EMAIL_VERIFY_LIMIT", 5)
EMAIL_RESET_LIMIT = env_int("EMAIL_RESET_LIMIT", 5)
EMAIL_IP_LIMIT = env_int("EMAIL_IP_LIMIT", 20)
EMAIL_BULK_LIMIT = env_int("EMAIL_BULK_LIMIT", 500)
EMAIL_RATE_WINDOW_SECONDS = env_int("EMAIL_RATE_WINDOW_SECONDS", 3600)

# A reset link expires in one hour and can only be used once (Django's view
# removes the token after a successful reset).
PASSWORD_RESET_TIMEOUT = env_int("PASSWORD_RESET_TIMEOUT", 3600)

# --- online payments (Paynow Zimbabwe) ------------------------------------
PAYNOW_INTEGRATION_ID = env_str("PAYNOW_INTEGRATION_ID", "")
PAYNOW_INTEGRATION_KEY = env_str("PAYNOW_INTEGRATION_KEY", "")
PAYNOW_MODE = env_str("PAYNOW_MODE", "test")
PAYNOW_RETURN_URL = env_str("PAYNOW_RETURN_URL", "")
PAYNOW_RESULT_URL = env_str("PAYNOW_RESULT_URL", "")

# --- fiscalisation (ZIMRA FDMS) -------------------------------------------
# The module is switched on per institution and only ever runs for a
# VAT-registered institution. It defaults to the mock gateway so the flow can
# be tested end to end; point it at ZIMRA once the endpoint and credentials
# are supplied.
FISCALISATION_ENABLED = env_bool("EDUSPHERE_FISCALISATION_ENABLED", True)
FISCALISATION_GATEWAY = env_str("EDUSPHERE_FISCALISATION_GATEWAY", "mock")
ZIMRA_FDMS_URL = env_str("EDUSPHERE_ZIMRA_FDMS_URL", "")
ZIMRA_DEVICE_ID = env_str("EDUSPHERE_ZIMRA_DEVICE_ID", "")
ZIMRA_DEVICE_SERIAL = env_str("EDUSPHERE_ZIMRA_DEVICE_SERIAL", "")
ZIMRA_API_KEY = env_str("EDUSPHERE_ZIMRA_API_KEY", "")

# --- encrypted financial backup -------------------------------------------
# A urlsafe base64 Fernet key. Generate one with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
FINANCE_BACKUP_KEY = env_str("EDUSPHERE_FINANCE_BACKUP_KEY", "")

# --- REST API and integrations --------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["api.authentication.ApiKeyAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": [
        "api.permissions.HasApiPermission",
        "api.permissions.IsInstitutionMember",
    ],
    "DEFAULT_PAGINATION_CLASS": "api.pagination.StandardPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_FILTER_BACKENDS": [
        "api.filters.SimpleFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.ScopedRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"api": "120/min", "api-write": "30/min"},
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "EduSphere API",
    "DESCRIPTION": (
        "A read-mostly REST API for one institution. Authenticate with an API "
        "key issued on the institution's API keys screen. The key decides the "
        "tenant, and every endpoint checks the account's role permissions."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "APPEND_COMPONENTS": {
        "securitySchemes": {
            "ApiKeyAuth": {
                "type": "apiKey",
                "in": "header",
                "name": "Authorization",
                "description": "Use the value `Api-Key esk_...`.",
            }
        }
    },
    "SECURITY": [{"ApiKeyAuth": []}],
    "ENUM_NAME_OVERRIDES": {
        "StudentStatusEnum": "api.choices.STUDENT_STATUS",
        "EnrollmentStatusEnum": "api.choices.ENROLMENT_STATUS",
        "AttendanceStatusEnum": "api.choices.ATTENDANCE_STATUS",
        "ApplicationStatusEnum": "api.choices.APPLICATION_STATUS",
    },
}
