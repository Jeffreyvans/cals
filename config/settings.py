from pathlib import Path
import secrets
import sys

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

if "test" in sys.argv:
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "accounts", "academics", "finance",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
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
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
TIME_ZONE = env_str("EDUSPHERE_TIME_ZONE", "Africa/Harare")
USE_TZ = True
INSTITUTION_NAME = env_str("EDUSPHERE_INSTITUTION_NAME",
                           "Lindenet Institute of Technology")   # change per school
CURRENCY = env_str("EDUSPHERE_CURRENCY", "$")
