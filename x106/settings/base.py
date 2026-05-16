"""Base Django settings shared by dev and production."""

from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(env_file)

DJANGO_ENV = env.str("DJANGO_ENV", default="development")
DEBUG = DJANGO_ENV != "production"

SECRET_KEY = env.str("DJANGO_SECRET_KEY", default="x106-django-dev-secret-change-me")

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["*"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "drf_spectacular",
    "apps.core",
    "apps.accounts",
    "apps.journal",
    "apps.studio",
    "apps.content",
    "apps.admin_art",
    "apps.ledger",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "x106.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "x106.wsgi.application"
ASGI_APPLICATION = "x106.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": env.str("DB_NAME", default="x106"),
        "USER": env.str("DB_USER", default="root"),
        "PASSWORD": env.str("DB_PASSWORD", default=""),
        "HOST": env.str("DB_HOST", default="127.0.0.1"),
        "PORT": env.str("DB_PORT", default="3306"),
        "OPTIONS": {
            "charset": "utf8mb4",
            "init_command": (
                "SET sql_mode='STRICT_TRANS_TABLES,NO_ZERO_DATE,"
                "NO_ZERO_IN_DATE,ERROR_FOR_DIVISION_BY_ZERO'"
            ),
        },
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
    }
}

# Existing user passwords are bcrypt-hashed by the Go service (golang.org/x/crypto/bcrypt).
# BCryptPasswordHasher (NOT BCryptSHA512PasswordHasher) verifies them as-is.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.BCryptPasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
]

AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 6}},
]

# Match the Go service's Asia/Ho_Chi_Minh wall-clock for daily streaks/quota.
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Ho_Chi_Minh"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ─── App-specific config ──────────────────────────────────────────────────

# Cookies
X106_COOKIE_DOMAIN = env.str("COOKIE_DOMAIN", default=None) or None
X106_SESSION_COOKIE = "x106_session"
X106_ADMIN_COOKIE = "x106_admin"
X106_SESSION_COOKIE_MAX_AGE = int(timedelta(days=30).total_seconds())
X106_ADMIN_COOKIE_MAX_AGE = int(timedelta(hours=8).total_seconds())

# DeepSeek
DEEPSEEK_API_KEY = env.str("DEEPSEEK_API_KEY", default="")
DEEPSEEK_BASE_URL = env.str("DEEPSEEK_BASE_URL", default="https://api.deepseek.com")
DEEPSEEK_MODEL = env.str("DEEPSEEK_MODEL", default="deepseek-v4-pro")
DEEPSEEK_FLASH_MODEL = env.str("DEEPSEEK_FLASH_MODEL", default="deepseek-v4-flash")
# OpenCode Go — same key works for both the OpenAI- and Anthropic-compatible
# endpoints; the bare /v1 root is the OpenAI-compat path, /v1/messages is
# Anthropic-compat (see services/providers.py).
OPENCODE_API_KEY = env.str("OPENCODE_API_KEY", default="")
OPENCODE_BASE_URL = env.str("OPENCODE_BASE_URL", default="https://opencode.ai/zen/go/v1")
OPENCODE_ANTHROPIC_BASE_URL = env.str(
    "OPENCODE_ANTHROPIC_BASE_URL",
    default="https://opencode.ai/zen/go/v1",
)
LLM_DAILY_LIMIT = env.int("LLM_DAILY_LIMIT", default=5)

# ─── DRF & SimpleJWT ──────────────────────────────────────────────────────

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.accounts.auth.JWTCookieAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.LimitOffsetPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(days=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=60),
    "ALGORITHM": "HS256",
    "SIGNING_KEY": env.str("JWT_SECRET", default="x106-dev-secret-change-in-production"),
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "TOKEN_TYPE_CLAIM": "token_type",
    "JTI_CLAIM": "jti",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "X106 API",
    "DESCRIPTION": "Backend for the X106 ecosystem (Django rewrite of the Go service).",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# ─── CORS ─────────────────────────────────────────────────────────────────

CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:3001",
    "http://localhost:3002",
    "http://localhost:3003",
    "http://localhost:3004",
    "http://localhost:3005",
    "https://kynguyen.cc",
    "https://me.kynguyen.cc",
    "https://journal.kynguyen.cc",
    "https://art.kynguyen.cc",
    "https://admin.kynguyen.cc",
    "https://ledger.kynguyen.cc",
]
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = ["content-type", "authorization"]
CORS_ALLOW_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]

# ─── Celery ───────────────────────────────────────────────────────────────

CELERY_BROKER_URL = env.str("REDIS_URL", default="redis://127.0.0.1:6379/0")
CELERY_RESULT_BACKEND = env.str("REDIS_URL", default="redis://127.0.0.1:6379/0")
CELERY_TASK_TIME_LIMIT = 620
CELERY_TASK_SOFT_TIME_LIMIT = 600
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_TRACK_STARTED = True
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    "recover-stale-llm-jobs": {
        "task": "apps.studio.maintenance.recover_stale_jobs",
        "schedule": 60.0,
    },
    "cleanup-old-llm-jobs": {
        "task": "apps.studio.maintenance.cleanup_old_jobs",
        "schedule": 1800.0,
    },
}

# ─── Logging ──────────────────────────────────────────────────────────────

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "concise": {"format": "[%(name)s] %(levelname)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "concise"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "handlers": ["console"], "propagate": False},
        "x106": {"level": "INFO", "handlers": ["console"], "propagate": False},
    },
}
