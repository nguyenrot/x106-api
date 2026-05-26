"""Artwork model — one row per saved Studio scene.

Snapshots ship the full `StudioSceneRecipe` JSON so the editor can rehydrate
identically on reload. Optional `share_token` opens an anonymous read-only
viewer at `/v/{token}` on the frontend.
"""

from __future__ import annotations

import secrets

from django.conf import settings
from django.db import models

from apps.core.ids import new_id


def _share_token() -> str:
    # 24-char URL-safe token. ~144 bits of entropy, plenty for a public
    # snapshot link that can be rotated by re-sharing.
    return secrets.token_urlsafe(18)


class Artwork(models.Model):
    KIND_CHOICES = [
        ("favorite", "favorite"),
        ("upload", "upload"),
        ("snapshot", "snapshot"),
    ]

    id = models.CharField(primary_key=True, max_length=36, default=new_id, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.DO_NOTHING,
        db_column="user_id",
        db_constraint=False,
        related_name="artworks",
    )
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default="snapshot")
    source_id = models.CharField(max_length=64, null=True, blank=True)
    title = models.CharField(max_length=120)
    prompt = models.TextField(default="", blank=True)
    style = models.CharField(max_length=64, default="3d-art-studio")
    palette = models.CharField(max_length=64, default="")
    seed = models.BigIntegerField(default=0)
    settings = models.JSONField(default=dict, blank=True)
    scene = models.JSONField(default=dict, blank=True)
    thumbnail_data_url = models.TextField()
    asset_data_url = models.TextField(null=True, blank=True)
    share_token = models.CharField(max_length=32, null=True, blank=True, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "artworks"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"], name="idx_artworks_user_created"),
        ]
