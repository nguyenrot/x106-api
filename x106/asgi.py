"""ASGI entry point (unused — production runs WSGI via gunicorn)."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "x106.settings.production")

application = get_asgi_application()
