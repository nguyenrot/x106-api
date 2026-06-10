"""Production settings."""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import env

# Hard-fail at import time if the secrets are missing or still the dev
# defaults from base.py — production must never run with guessable keys.
_DEV_SECRET_KEY = "x106-django-dev-secret-change-me"
_DEV_JWT_SECRET = "x106-dev-secret-change-in-production"

_secret_key = env.str("DJANGO_SECRET_KEY", default="").strip()
if not _secret_key or _secret_key == _DEV_SECRET_KEY:
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY must be set to a real value in production (.env)."
    )

_jwt_secret = env.str("JWT_SECRET", default="").strip()
if not _jwt_secret or _jwt_secret == _DEV_JWT_SECRET:
    raise ImproperlyConfigured(
        "JWT_SECRET must be set to a real value in production (.env)."
    )

del _secret_key, _jwt_secret

DEBUG = False
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["api.kynguyen.cc"])

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 0  # Cloudflare handles HSTS at the edge
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
