import logging
import os
import secrets
import sys
from email.utils import parseaddr
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

from . import brand
from .email_config import select_email_settings
from .env import env_bool, env_int, env_list, env_str, load_dotenv

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# Product identity. ``BRAND_NAME`` overrides the default set in config/brand.py.
# Environment variable names and internal identifiers keep their old spelling.
BRAND_NAME = brand.PRODUCT_NAME
BRAND_COMPANY = brand.COMPANY_NAME


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

# Optional AI assistance. Model names and the key are read from the environment
# at call time in ai/providers.py; no model name or secret lives in the codebase.
# A provider call is capped at 15 seconds by default and never uses Celery.
AI_PROVIDER = env_str("AI_PROVIDER", "anthropic")
AI_TIMEOUT_SECONDS = env_int("AI_TIMEOUT_SECONDS", 15)
# Short-window burst limits, separate from the daily/monthly log limits.
AI_RATE_LIMIT = env_int("AI_RATE_LIMIT", 10)
AI_RATE_IP_LIMIT = env_int("AI_RATE_IP_LIMIT", 30)
AI_RATE_WINDOW_SECONDS = env_int("AI_RATE_WINDOW_SECONDS", 60)
AI_PROVIDER_INSTANCE = None  # test injection point; never configured in production

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
    "enterprise",
    "fiscalisation",
    # Optional AI assistance. Every feature is off until switches, consent and
    # the platform kill switch are explicitly enabled; see docs/AI.md.
    "ai",
    "rest_framework", "drf_spectacular", "anymail", "api",
]
MIDDLEWARE = [
    # Verified custom domains must be registered before get_host() is used.
    "core.middleware.CustomDomainHostMiddleware",
    "django.middleware.security.SecurityMiddleware",
    # 301 from an old domain to SITE_URL when OLD_SITE_URL is configured.
    "core.middleware.OldDomainRedirectMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    # Compress HTML, JSON and CSV on the fly. WhiteNoise already serves the
    # collected static files pre-compressed; this covers dynamic responses.
    "django.middleware.gzip.GZipMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    # Cheap ETag/304 handling for public pages and JSON.
    "django.middleware.http.ConditionalGetMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.middleware.HostSessionIsolationMiddleware",
    "accounts.middleware.ForcePasswordChangeMiddleware",
    "core.middleware.FirstSignInSetupMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    # Classify, record and log every failure before views run, so failures in
    # later middleware are captured too.
    "core.error_middleware.ErrorContextMiddleware",
    "core.middleware.LowDataMiddleware",
    "core.middleware.InstitutionMiddleware",
    "platform_admin.middleware.PlatformDataAccessMiddleware",
    # Last, so it sees the final response and can record its size and timing.
    "core.middleware.ResponseMetricsMiddleware",
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
        "core.errors.support_context",
        "config.brand.brand_context",
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
        "NAME": BASE_DIR / env_str("EDUSPHERE_DB_PATH", "db.sqlite3"),
        # Imports and background workers can write at the same time.
        # Waiting on the SQLite write lock avoids transient "database is locked"
        # errors during concurrent number allocation.
        "OPTIONS": {"timeout": 30}}}
AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"   # target of collectstatic
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / env_str("EDUSPHERE_MEDIA_PATH", "media")

# Upload limits. The whole request is capped before Django buffers it, and the
# per-file limits in ``core.uploads`` produce the friendly message a person
# sees when their picture or document is too large.
DATA_UPLOAD_MAX_MEMORY_SIZE = env_int("DATA_UPLOAD_MAX_MEMORY_SIZE", 12 * 1024 * 1024)
FILE_UPLOAD_MAX_MEMORY_SIZE = env_int("FILE_UPLOAD_MAX_MEMORY_SIZE", 5 * 1024 * 1024)
DATA_UPLOAD_MAX_NUMBER_FIELDS = env_int("DATA_UPLOAD_MAX_NUMBER_FIELDS", 2000)

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
    AWS_QUERYSTRING_AUTH = env_bool("AWS_QUERYSTRING_AUTH", True)
    AWS_QUERYSTRING_EXPIRE = env_int("AWS_QUERYSTRING_EXPIRE", 3600)
    AWS_S3_SIGNATURE_VERSION = "s3v4"
    AWS_S3_MAX_MEMORY_SIZE = 5 * 1024 * 1024
    AWS_LOCATION = _first("AWS_LOCATION", "EDUSPHERE_S3_PREFIX").strip()
    # Uploaded pictures and documents are immutable (the stored name contains a
    # random token), so a browser and the CDN may keep them for a year.
    AWS_S3_OBJECT_PARAMETERS = {
        "CacheControl": "public, max-age=31536000, immutable",
    }

    _s3_options = {
        "bucket_name": AWS_STORAGE_BUCKET_NAME,
        "region_name": AWS_S3_REGION_NAME,
        "default_acl": None,
        "file_overwrite": False,
        "querystring_auth": AWS_QUERYSTRING_AUTH,
        "querystring_expire": AWS_QUERYSTRING_EXPIRE,
        "signature_version": AWS_S3_SIGNATURE_VERSION,
        "max_memory_size": AWS_S3_MAX_MEMORY_SIZE,
        "object_parameters": AWS_S3_OBJECT_PARAMETERS,
    }
    if AWS_S3_ENDPOINT_URL:
        _s3_options["endpoint_url"] = AWS_S3_ENDPOINT_URL
    if AWS_S3_CUSTOM_DOMAIN:
        _s3_options["custom_domain"] = AWS_S3_CUSTOM_DOMAIN
    if AWS_LOCATION:
        _s3_options["location"] = AWS_LOCATION
    if not DEBUG and not TESTING:
        missing = [
            name
            for name, value in (
                ("AWS_STORAGE_BUCKET_NAME", AWS_STORAGE_BUCKET_NAME),
                ("AWS_ACCESS_KEY_ID", AWS_ACCESS_KEY_ID),
                ("AWS_SECRET_ACCESS_KEY", AWS_SECRET_ACCESS_KEY),
            )
            if not value
        ]
        if STORAGE_BACKEND in {"r2", "b2", "supabase", "object"} and not AWS_S3_ENDPOINT_URL:
            missing.append("AWS_S3_ENDPOINT_URL")
        if missing:
            raise ImproperlyConfigured(
                "STORAGE_BACKEND is set to "
                f"{STORAGE_BACKEND!r} but these settings are empty: "
                + ", ".join(missing)
                + ". Uploads would fail in production. Set them in the "
                "environment (see DEPLOY.md)."
            )
    STORAGES = {
        "default": {"BACKEND": "storages.backends.s3.S3Storage", "OPTIONS": _s3_options},
        "staticfiles": {"BACKEND": STATICFILES_BACKEND},
    }
else:
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": STATICFILES_BACKEND},
    }

# A production boot must never quietly write uploads to the container disk:
# Render throws the filesystem away on the next deploy, so every logo and
# document would disappear. Fail loudly instead, with the variables to set.
from core.uploads import validate_storage_configuration  # noqa: E402

validate_storage_configuration(
    debug=DEBUG, testing=TESTING, backend=STORAGE_BACKEND
)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
TIME_ZONE = env_str("EDUSPHERE_TIME_ZONE", "Africa/Harare")
USE_TZ = True
INSTITUTION_NAME = env_str("EDUSPHERE_INSTITUTION_NAME",
                           "Lindenet Institute of Technology")   # change per school
CURRENCY = env_str("EDUSPHERE_CURRENCY", "$")

# --- cache -----------------------------------------------------------------
# Redis is used when REDIS_URL is set (Render Redis, Upstash, ...). Without it
# the process-local memory cache still removes repeated permission, branding and
# dashboard queries, which is what a single small deployment needs. The cache
# is deliberately conservative: nothing that must be instantly consistent is
# stored for longer than a few minutes, and writes clear the keys they affect.
REDIS_URL = _first("REDIS_URL", "EDUSPHERE_REDIS_URL").strip()
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
            "TIMEOUT": env_int("CACHE_TIMEOUT", 300),
            "KEY_PREFIX": env_str("CACHE_KEY_PREFIX", "edusphere"),
            "OPTIONS": {"socket_connect_timeout": 2, "socket_timeout": 2},
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "edusphere-local",
            "TIMEOUT": env_int("CACHE_TIMEOUT", 300),
            "OPTIONS": {"MAX_ENTRIES": 5000},
        }
    }

# Branding and public pages change rarely; these are the TTLs (seconds) that
# the cache helpers use. A settings save clears the affected institution.
CACHE_TTL_PERMISSIONS = env_int("CACHE_TTL_PERMISSIONS", 300)
CACHE_TTL_BRANDING = env_int("CACHE_TTL_BRANDING", 900)
CACHE_TTL_PUBLIC = env_int("CACHE_TTL_PUBLIC", 600)
CACHE_TTL_DASHBOARD = env_int("CACHE_TTL_DASHBOARD", 120)

# --- background jobs (Celery) ---------------------------------------------
# PDF reports, bulk email, imports and exports run on a worker so a request
# never waits for them. With no broker configured the same functions run inline
# (and the UI says so), so a small installation still works out of the box.
CELERY_BROKER_URL = _first("CELERY_BROKER_URL", "REDIS_URL").strip() or REDIS_URL
CELERY_RESULT_BACKEND = _first(
    "CELERY_RESULT_BACKEND", default=CELERY_BROKER_URL or "cache+memory://"
)
CELERY_TASK_ALWAYS_EAGER = env_bool(
    "CELERY_TASK_ALWAYS_EAGER", not bool(CELERY_BROKER_URL)
)
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = env_int("CELERY_TASK_TIME_LIMIT", 600)
CELERY_TASK_SOFT_TIME_LIMIT = env_int("CELERY_TASK_SOFT_TIME_LIMIT", 540)
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

# --- logging, slow queries and error reporting ----------------------------
# One structured line per slow request and slow database query, so a free-tier
# log viewer still shows the problem. Sentry is opt-in: set SENTRY_DSN and the
# SDK (if installed) receives the same events.
SLOW_REQUEST_SECONDS = float(env_str("SLOW_REQUEST_SECONDS", "1.5"))
SLOW_QUERY_SECONDS = float(env_str("SLOW_QUERY_SECONDS", "0.25"))
LOG_LEVEL = env_str("LOG_LEVEL", "INFO").upper()

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "plain",
        },
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django.request": {
            "handlers": ["console"], "level": "WARNING", "propagate": False,
        },
        "django.security": {
            "handlers": ["console"], "level": "WARNING", "propagate": False,
        },
        "edusphere.errors": {
            "handlers": ["console"], "level": "INFO", "propagate": False,
        },
        "edusphere.slow": {
            "handlers": ["console"], "level": "INFO", "propagate": False,
        },
        "edusphere.uploads": {
            "handlers": ["console"], "level": "INFO", "propagate": False,
        },
    },
}

SENTRY_DSN = _first("SENTRY_DSN", "EDUSPHERE_SENTRY_DSN").strip()
SENTRY_ENVIRONMENT = _first("SENTRY_ENVIRONMENT", default="production" if not DEBUG else "development")
SENTRY_TRACES_SAMPLE_RATE = float(env_str("SENTRY_TRACES_SAMPLE_RATE", "0.05"))
if SENTRY_DSN:
    try:  # pragma: no cover - the package is optional in development
        import sentry_sdk
        from sentry_sdk.integrations.django import DjangoIntegration

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            environment=SENTRY_ENVIRONMENT,
            integrations=[DjangoIntegration()],
            traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
            send_default_pii=False,
        )
    except ImportError:
        logger.warning(
            "SENTRY_DSN is set but sentry-sdk is not installed; install "
            "sentry-sdk[django] to send errors to Sentry."
        )

# --- email ----------------------------------------------------------------
# Render blocks outbound SMTP on ports 25, 465 and 587. Every message goes
# through Django's single configured email backend, using an HTTPS API via
# django-anymail (Mailjet by default in production), the console backend
# locally, and Django's in-memory backend in tests. There are deliberately no
# EMAIL_HOST/PORT/TLS settings: nothing in this project can open SMTP.

# A slow provider must never freeze a request.
EMAIL_TIMEOUT = env_int("EMAIL_TIMEOUT", 10)
_email_config = select_email_settings(os.environ, timeout=EMAIL_TIMEOUT, debug=DEBUG)
EMAIL_PROVIDER = _email_config["EMAIL_PROVIDER"]
EMAIL_BACKEND = _email_config["EMAIL_BACKEND"]
ANYMAIL = _email_config["ANYMAIL"]
RESEND_API_KEY = _email_config["RESEND_API_KEY"]
MAILJET_API_KEY = _email_config["MAILJET_API_KEY"]
MAILJET_SECRET_KEY = _email_config["MAILJET_SECRET_KEY"]
BREVO_API_KEY = _email_config["BREVO_API_KEY"]
MAILJET_API_URL = _email_config["MAILJET_API_URL"]
RESEND_API_URL = _email_config["RESEND_API_URL"]
BREVO_API_URL = _email_config["BREVO_API_URL"]

EMAIL_FROM_DOMAIN = _first(
    "EMAIL_FROM_DOMAIN", "EDUSPHERE_EMAIL_FROM_DOMAIN",
    default="edusphere.local",
).strip()
DEFAULT_FROM_EMAIL = _first(
    "DEFAULT_FROM_EMAIL", "EDUSPHERE_EMAIL_FROM",
    default=f"{BRAND_NAME} <no-reply@{EMAIL_FROM_DOMAIN}>",
).strip()
SERVER_EMAIL = _first(
    "SERVER_EMAIL", "EDUSPHERE_SERVER_EMAIL", default=DEFAULT_FROM_EMAIL,
).strip()
DEFAULT_REPLY_TO_EMAIL = _first(
    "DEFAULT_REPLY_TO_EMAIL", "EDUSPHERE_REPLY_TO_EMAIL", "REPLY_TO_EMAIL",
).strip()
EMAIL_FROM = DEFAULT_FROM_EMAIL   # backwards-compatible alias used by old code

RESEND_WEBHOOK_SECRET = _first(
    "RESEND_WEBHOOK_SECRET", "EDUSPHERE_RESEND_WEBHOOK_SECRET",
).strip()
MAILJET_WEBHOOK_SECRET = _first(
    "MAILJET_WEBHOOK_SECRET", "EDUSPHERE_MAILJET_WEBHOOK_SECRET",
).strip()

# Startup warnings: never fail a request, but make a bad deployment obvious in
# the logs. Free mailbox senders are rejected or throttled by most ESPs.
_FREE_MAILBOX_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "hotmail.com",
    "outlook.com", "live.com", "msn.com", "aol.com", "icloud.com", "me.com",
    "mail.com", "gmx.com", "proton.me", "protonmail.com", "zoho.com",
}
_from_address = parseaddr(DEFAULT_FROM_EMAIL)[1].strip().lower()
_from_domain = _from_address.rsplit("@", 1)[-1] if "@" in _from_address else ""
if _from_domain in _FREE_MAILBOX_DOMAINS:
    _email_config["warnings"].append(
        f"DEFAULT_FROM_EMAIL uses the free mailbox domain {_from_domain}; verify "
        "a domain you own with Mailjet and use no-reply@your-domain instead."
    )
for _warning in _email_config["warnings"]:
    logger.warning("Email configuration: %s", _warning)

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

# Optional permanent redirect from an old domain after a rename or a custom
# domain change. Empty by default, so every existing URL keeps working. Set a
# comma-separated list of old hosts (for example OLD_SITE_URL=old.example.com)
# and requests to them get a 301 to the same path on SITE_URL.
OLD_SITE_URL = env_list("OLD_SITE_URL", [])

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

# Student bulk upload. Both limits are configurable so a small instance can be
# stricter and a large one can raise them without a code change.
BULK_UPLOAD_MAX_BYTES = env_int("BULK_UPLOAD_MAX_BYTES", 5 * 1024 * 1024)
BULK_UPLOAD_MAX_ROWS = env_int("BULK_UPLOAD_MAX_ROWS", 5000)
BULK_UPLOAD_EMAIL_CHUNK = env_int("BULK_UPLOAD_EMAIL_CHUNK", 50)

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
    "EXCEPTION_HANDLER": "core.errors.api_exception_handler",
}

SPECTACULAR_SETTINGS = {
    "TITLE": f"{BRAND_NAME} API",
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
