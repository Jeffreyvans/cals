import os
import secrets
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from .env import env_bool, env_int, env_list, env_str, load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DEBUG = env_bool("EDUSPHERE_DEBUG", True)

SECRET_KEY = env_str("EDUSPHERE_SECRET_KEY", "").strip()
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = secrets.token_urlsafe(50)   # throwaway dev key
    else:
        raise ImproperlyConfigured(
            "EDUSPHERE_SECRET_KEY must be set when EDUSPHERE_DEBUG is off. "
            "Generate one with: python -c \"import secrets; "
            "print(secrets.token_urlsafe(50))\"")

ALLOWED_HOSTS = env_list("EDUSPHERE_ALLOWED_HOSTS", ["*"] if DEBUG else [])
if not DEBUG and not ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        "EDUSPHERE_ALLOWED_HOSTS must list your domain(s) when "
        "EDUSPHERE_DEBUG is off, e.g. EDUSPHERE_ALLOWED_HOSTS=fees.school.org")
CSRF_TRUSTED_ORIGINS = env_list("EDUSPHERE_CSRF_TRUSTED_ORIGINS", [])

if env_bool("EDUSPHERE_SSL", False):
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = env_int("EDUSPHERE_HSTS_SECONDS", 0)

if "test" in sys.argv or "pytest" in sys.modules or "PYTEST_CURRENT_TEST" in os.environ:
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
    "fiscalisation",
    "rest_framework", "drf_spectacular", "api",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.middleware.ForcePasswordChangeMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "core.middleware.InstitutionMiddleware",
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
    ]},
}]
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
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
TIME_ZONE = env_str("EDUSPHERE_TIME_ZONE", "Africa/Harare")
USE_TZ = True
INSTITUTION_NAME = env_str("EDUSPHERE_INSTITUTION_NAME",
                           "Lindenet Institute of Technology")   # change per school
CURRENCY = env_str("EDUSPHERE_CURRENCY", "$")

# --- email ----------------------------------------------------------------
# Development prints email to the console; production uses SMTP. The test
# runner swaps in Django's in-memory backend automatically.
EMAIL_BACKEND = env_str(
    "EDUSPHERE_EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend"
    if DEBUG else "django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = env_str("EDUSPHERE_EMAIL_HOST", "localhost")
EMAIL_PORT = env_int("EDUSPHERE_EMAIL_PORT", 25)
EMAIL_HOST_USER = env_str("EDUSPHERE_EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env_str("EDUSPHERE_EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EDUSPHERE_EMAIL_USE_TLS", False)
EMAIL_USE_SSL = env_bool("EDUSPHERE_EMAIL_USE_SSL", False)
EMAIL_FROM = env_str("EDUSPHERE_EMAIL_FROM", "EduSphere <no-reply@edusphere.local>")
DEFAULT_FROM_EMAIL = EMAIL_FROM

# Resend is used for account invitations when a key is configured. Without a
# key the existing console/SMTP provider is used, so development still works.
RESEND_API_KEY = env_str("EDUSPHERE_RESEND_API_KEY", "")
RESEND_FROM = env_str("EDUSPHERE_RESEND_FROM", EMAIL_FROM)
RESEND_API_URL = env_str("EDUSPHERE_RESEND_API_URL", "https://api.resend.com/emails")

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
