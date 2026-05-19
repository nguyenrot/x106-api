"""Vibe + StreakFreeze models — one journal entry per (user, date), plus
optional "freeze" rows that let a missed day still count toward the streak.
"""

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


class StreakFreeze(models.Model):
    """One row per (user, applied_date). Marks a day as "frozen" — the streak
    walker treats it the same as a day with a vibe."""

    id = models.CharField(primary_key=True, max_length=36, default=new_id, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.DO_NOTHING,
        db_column="user_id",
        db_constraint=False,
        related_name="streak_freezes",
    )
    applied_date = models.DateField()
    used_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "streak_freezes"
        constraints = [
            models.UniqueConstraint(fields=["user", "applied_date"], name="uq_freeze_user_date"),
        ]
        ordering = ["-applied_date"]
