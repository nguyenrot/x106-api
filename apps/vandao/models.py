"""Cloud save for Vấn Đạo — one blob per player.

The game is an offline-first SPA: the authoritative copy lives in the browser's
`localStorage` (`vandao-save-v1`) and this table is a per-account mirror so progress can
follow the player to another device. The API deliberately knows nothing about the save's
shape — the game owns its schema and evolves it without a migration here.

`revision` is what keeps two devices from silently overwriting each other: a client sends
the revision it last saw, and a mismatch is a 409 the player resolves rather than a lost
afternoon of cultivating.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class GameSave(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        primary_key=True,
        on_delete=models.CASCADE,
        db_column="user_id",
        related_name="vandao_save",
    )
    data = models.JSONField()
    # Bumped on every accepted write; the client stores the value it last synced.
    revision = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "vandao_saves"

    def __str__(self) -> str:
        return f"{self.user_id} r{self.revision}"
