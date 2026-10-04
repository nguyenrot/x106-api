"""Base Django settings shared by dev and production."""

from datetime import timedelta
from pathlib import Path

import environ
from celery.schedules import crontab

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
    "apps.content",
    "apps.ops",
    "apps.cafe",
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

# Local fallback for uploaded images when GitHub/jsDelivr isn't configured
# (dev). In prod, CAFE_IMAGE_GITHUB_* is set and uploads never touch disk.
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ─── App-specific config ──────────────────────────────────────────────────

# Cookies
X106_COOKIE_DOMAIN = env.str("COOKIE_DOMAIN", default=None) or None
X106_SESSION_COOKIE = "x106_session"
X106_ADMIN_COOKIE = "x106_admin"
X106_SESSION_COOKIE_MAX_AGE = int(timedelta(days=30).total_seconds())
X106_ADMIN_COOKIE_MAX_AGE = int(timedelta(hours=8).total_seconds())

# ─── Sign in with Google (apps.accounts.google) ───────────────────────────
#
# One "Web application" OAuth client shared by every X106 frontend that offers
# Google sign-in. Its authorized JavaScript origins must list each of those
# origins; it must have NO authorized redirect URI — the popup code flow
# exchanges with `redirect_uri=postmessage`. Blank disables POST /auth/google
# (503) and the frontends hide the button.
GOOGLE_OAUTH_CLIENT_ID = env.str("GOOGLE_OAUTH_CLIENT_ID", default="")
GOOGLE_OAUTH_CLIENT_SECRET = env.str("GOOGLE_OAUTH_CLIENT_SECRET", default="")
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

# ─── Public image hosting (apps.cafe uploads) ─────────────────────────────
#
# Cafe review covers/gallery are pushed to a public GitHub repo and served via
# jsDelivr when a token is set; otherwise they fall back to local MEDIA_ROOT.
# Set CAFE_IMAGE_GITHUB_TOKEN (a fine-grained PAT with Contents:write on the
# repo) in /var/www/api/.env on the VPS.
CAFE_IMAGE_GITHUB_TOKEN = env.str("CAFE_IMAGE_GITHUB_TOKEN", default="")
CAFE_IMAGE_GITHUB_REPO = env.str("CAFE_IMAGE_GITHUB_REPO", default="nguyenrot/cafe-image")
CAFE_IMAGE_GITHUB_BRANCH = env.str("CAFE_IMAGE_GITHUB_BRANCH", default="main")

# Origin prepended to a local MEDIA_URL path when an image URL has to work from
# another host (the frontends are on *.kynguyen.cc, the API is not). Only the
# local-storage fallback needs it — jsDelivr URLs are already absolute. Kept as
# a setting rather than derived from the request because the same URLs are
# minted from Celery tasks and management commands, where there is no request,
# and the value has to match byte-for-byte across all three (it is the join key
# between `cafe_reviews` and `cafe_images`).
PUBLIC_MEDIA_ORIGIN = env.str("PUBLIC_MEDIA_ORIGIN", default="https://api.kynguyen.cc")

# ─── Service tokens ───────────────────────────────────────────────────────
#
# Long-lived tokens for non-human callers (cron agents). We only ever store
# the SHA-256 hex of the raw token — the agent keeps the raw value in its
# .env on the VPS and sends it in `X-Service-Token` per request.
#
# To register a new service: pick a name, compute the SHA-256, set
# `SERVICE_TOKEN_<NAME>_SHA256` env var (uppercased, hyphens → underscores).
# Reload the api service to pick it up.
#
#   raw=$(openssl rand -hex 32)
#   printf '%s' "$raw" | shasum -a 256
#   # set SERVICE_TOKEN_QUOTES_AGENT_SHA256=<hash> in /var/www/api/.env
#
# quotes-agent (the only consumer) went away with apps.quotes on 2026-10-04;
# the mechanism stays for the next machine-to-machine caller.
_REGISTERED_SERVICES: list[str] = []
SERVICE_TOKENS: dict[str, str] = {
    svc: digest.strip().lower()
    for svc in _REGISTERED_SERVICES
    if (digest := env.str("SERVICE_TOKEN_" + svc.upper().replace("-", "_") + "_SHA256", default=""))
}

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
    # Opt-in per view (ScopedRateThrottle is not a default class) — currently only
    # POST /auth/google, which spends an outbound call to Google on every request.
    "DEFAULT_THROTTLE_RATES": {"auth_google": "30/hour"},
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
    "http://localhost:3003",
    "http://localhost:3006",
    "http://localhost:3010",
    "https://kynguyen.cc",
    "https://me.kynguyen.cc",
    "https://admin.kynguyen.cc",
    "https://cafe.kynguyen.cc",
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
    # 08:30 giờ Việt Nam — agent tổng hợp 1 bài review quán cà phê Đà Nẵng
    # (no-op khi CAFE_AGENT_ENABLED=false).
    "cafe-agent-daily-review": {
        "task": "apps.cafe.tasks.generate_cafe_review",
        "schedule": crontab(hour=8, minute=30),
        "args": ("daily",),
    },
}

# ─── Cafe review agent ────────────────────────────────────────────────────

CAFE_AGENT_ENABLED = env.bool("CAFE_AGENT_ENABLED", default=False)
CAFE_AGENT_MIN_CONFIDENCE = env.float("CAFE_AGENT_MIN_CONFIDENCE", default=0.7)

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
