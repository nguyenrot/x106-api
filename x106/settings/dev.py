"""Development settings."""

from .base import *  # noqa: F401,F403

DEBUG = True
ALLOWED_HOSTS = ["*"]

# In dev, scope cookies to whatever host the browser is on (localhost).
X106_COOKIE_DOMAIN = None

# Open CORS for any origin during dev (matches Go's IsDev() relaxed allowlist).
CORS_ALLOW_ALL_ORIGINS = True
