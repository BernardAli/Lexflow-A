"""LexFlow AI — Django settings.

Environment toggles (no code changes required):
  DJANGO_DEBUG=0|1                   default 1 for the prototype
  DJANGO_SECRET_KEY=...              required in production
  DJANGO_ALLOWED_HOSTS=a,b           comma separated
  USE_POSTGRES=1                     switch to PostgreSQL (POSTGRES_DB/USER/PASSWORD/HOST/PORT)
  LEXFLOW_MASTER_KEY=...             master key that wraps every tenant data key
  LEXFLOW_AI_PROVIDER=rules|anthropic  extraction engine (default: rules, runs fully offline)
  ANTHROPIC_API_KEY=...              only needed for the anthropic provider
  LEXFLOW_ANTHROPIC_MODEL=...        default claude-sonnet-5-5
"""
import os
import json
import sys
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "dev-only-lexflow-prototype-key-change-me-0c9f7e5a1b2d4e6f8a0b1c2d3e4f5a6b",
)
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = [h for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",") if h]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "apps.core",
    "apps.audit",
    "apps.studio",
    "apps.intake",
    "apps.matters",
    "apps.connectors",
]

MIDDLEWARE = [
    "apps.core.middleware.MaintenanceMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.TenantMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.lexflow",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

if os.environ.get("USE_POSTGRES") == "1":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("POSTGRES_DB", "lexflow"),
            "USER": os.environ.get("POSTGRES_USER", "lexflow"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
            "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": Path(os.environ.get("LEXFLOW_DB_PATH", BASE_DIR / "db.sqlite3")),
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

# Development prints reset emails locally; deployed offices configure SMTP.
EMAIL_BACKEND = os.environ.get(
    "DJANGO_EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend" if DEBUG else "django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_SSL = os.environ.get("EMAIL_USE_SSL", "0") == "1"
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "0" if EMAIL_USE_SSL else "1") == "1"
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "LexFlow <noreply@localhost>")
PASSWORD_RESET_TIMEOUT = 3600

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Europe/Berlin"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
TESTING = "test" in sys.argv
if not DEBUG and not TESTING:
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"},
    }

# Encrypted document blobs live under a per-tenant directory.
LEXFLOW_VAULT_ROOT = Path(os.environ.get("LEXFLOW_VAULT_ROOT", BASE_DIR / "vault"))
if TESTING:
    LEXFLOW_VAULT_ROOT = BASE_DIR / ".test-vault"

LEXFLOW_MASTER_KEY = os.environ.get("LEXFLOW_MASTER_KEY", "")
LEXFLOW_AI_PROVIDER = os.environ.get("LEXFLOW_AI_PROVIDER", "rules")
LEXFLOW_ANTHROPIC_MODEL = os.environ.get("LEXFLOW_ANTHROPIC_MODEL", "claude-sonnet-5-5")
LEXFLOW_MAX_UPLOAD_MB = 20
LEXFLOW_MAX_TOTAL_MB = 60
LEXFLOW_MAX_FILES = 20
LEXFLOW_EXPORT_ROOT = Path(os.environ.get("LEXFLOW_EXPORT_ROOT", BASE_DIR / "exports"))
LEXFLOW_DEMO_MODE = os.environ.get("LEXFLOW_DEMO_MODE", "1" if DEBUG else "0") == "1"
LEXFLOW_DEPLOYMENT = os.environ.get("LEXFLOW_DEPLOYMENT", "development")
LEXFLOW_TENANT_SLUG = os.environ.get("LEXFLOW_TENANT_SLUG", "")
LEXFLOW_OCR_ENABLED = os.environ.get("LEXFLOW_OCR_ENABLED", "0") == "1"
LEXFLOW_TESSERACT = os.environ.get("LEXFLOW_TESSERACT", "tesseract")
LEXFLOW_PDFTOPPM = os.environ.get("LEXFLOW_PDFTOPPM", "pdftoppm")
LEXFLOW_OCR_LANGUAGES = os.environ.get("LEXFLOW_OCR_LANGUAGES", "deu+eng")
LEXFLOW_OCR_TIMEOUT = 20
LEXFLOW_OCR_MAX_PAGES = 12
LEXFLOW_OCR_MAX_PIXELS = 20_000_000
LEXFLOW_OCR_TEMP_ROOT = os.environ.get("LEXFLOW_OCR_TEMP_ROOT") or None
# Exact endpoint URLs are set by the deployment operator, never by request data.
LEXFLOW_WEBHOOK_ENDPOINTS = json.loads(os.environ.get("LEXFLOW_WEBHOOK_ENDPOINTS", "{}"))
if not isinstance(LEXFLOW_WEBHOOK_ENDPOINTS, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in LEXFLOW_WEBHOOK_ENDPOINTS.items()):
    raise ImproperlyConfigured("LEXFLOW_WEBHOOK_ENDPOINTS must be a JSON object mapping endpoint keys to exact URLs.")
LEXFLOW_WEBHOOK_ALLOW_LOOPBACK = DEBUG and os.environ.get("LEXFLOW_WEBHOOK_ALLOW_LOOPBACK", "0") == "1"
LEXFLOW_WEBHOOK_TIMEOUT = 8
LEXFLOW_MAINTENANCE = os.environ.get("LEXFLOW_MAINTENANCE", "0") == "1"
LEXFLOW_BACKUP_KEY = os.environ.get("LEXFLOW_BACKUP_KEY", "")
LEXFLOW_BACKUP_MAX_BYTES = 256 * 1024 * 1024
LEXFLOW_EVALUATION_FONT = os.environ.get("LEXFLOW_EVALUATION_FONT", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
if TESTING:
    LEXFLOW_EXPORT_ROOT = BASE_DIR / ".test-exports"
if not DEBUG:
    if SECRET_KEY.startswith("dev-only-") or len(SECRET_KEY) < 50:
        raise ImproperlyConfigured("Set a unique DJANGO_SECRET_KEY of at least 50 characters for deployment.")
    if not LEXFLOW_MASTER_KEY.startswith("fernet:"):
        raise ImproperlyConfigured("Set LEXFLOW_MASTER_KEY to fernet:<generated key> for deployment.")
    from cryptography.fernet import Fernet
    try:
        Fernet(LEXFLOW_MASTER_KEY[7:].encode())
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured("LEXFLOW_MASTER_KEY is not a valid Fernet key.") from exc
    if LEXFLOW_DEMO_MODE:
        raise ImproperlyConfigured("Demo accounts and seeding must be disabled for deployment.")
    if LEXFLOW_DEPLOYMENT != "dedicated" or not LEXFLOW_TENANT_SLUG:
        raise ImproperlyConfigured("The supported pilot deployment is dedicated to one configured office.")

DATA_UPLOAD_MAX_MEMORY_SIZE = LEXFLOW_MAX_UPLOAD_MB * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = DATA_UPLOAD_MAX_MEMORY_SIZE

LOGIN_URL = "core:login"
LOGIN_REDIRECT_URL = "core:dashboard"
LOGOUT_REDIRECT_URL = "core:login"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False
X_FRAME_OPTIONS = "SAMEORIGIN"
SECURE_CONTENT_TYPE_NOSNIFF = True
if os.environ.get("DJANGO_TRUST_PROXY") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = False
    X_FRAME_OPTIONS = "DENY"
