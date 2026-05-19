"""Vibe model — one journal entry per (user, date)."""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.core.ids import new_id


class Vibe(models.Model):
    id = models.CharField(primary_key=True, max_length=36, default=new_id, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.DO_NOTHING,
        db_column="user_id",
        db_constraint=False,  # production has the FK; dev recreates may not
        related_name="vibes",
    )
    date = models.DateField()
    mood_emoji = models.CharField(max_length=10)
    title = models.CharField(max_length=255)
    note = models.TextField(null=True, blank=True)
    tags = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "vibes"
        constraints = [
            models.UniqueConstraint(fields=["user", "date"], name="uq_vibes_user_date"),
        ]
        ordering = ["-date"]
