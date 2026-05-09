"""Celery app for x106."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "x106.settings.production")

celery_app = Celery("x106")
celery_app.config_from_object("django.conf:settings", namespace="CELERY")
celery_app.autodiscover_tasks()
