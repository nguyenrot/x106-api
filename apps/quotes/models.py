"""Quote + QuoteFavorite — personal quote library shared via quotes.kynguyen.cc."""

from __future__ import annotations

from secrets import token_urlsafe

from django.conf import settings
from django.db import models

from apps.core.ids import new_id


def _new_share_token() -> str:
    """Kept for migration 0001_initial which referenced this default.
    The Baogia/BaogiaLineItem models that used it were dropped in 0002."""
    return token_urlsafe(24)


class Quote(models.Model):
    id = models.CharField(primary_key=True, max_length=36, default=new_id, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.DO_NOTHING,
        db_column="user_id",
        db_constraint=False,
        null=True,
        blank=True,
        related_name="quotes",
    )
    body = models.TextField()
    author = models.CharField(max_length=200, blank=True, default="")
    source = models.CharField(max_length=500, blank=True, default="")
    tags = models.JSONField(default=list, blank=True)
    language = models.CharField(max_length=8, default="vi")
    is_public = models.BooleanField(default=False)
    is_curated = models.BooleanField(default=False)
    is_featured = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "quotes"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["is_curated", "language"], name="ix_quotes_curated_lang"),
            models.Index(fields=["user", "is_public"], name="ix_quotes_user_public"),
            models.Index(fields=["is_featured"], name="ix_quotes_featured"),
        ]


class QuoteFavorite(models.Model):
    id = models.CharField(primary_key=True, max_length=36, default=new_id, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.DO_NOTHING,
        db_column="user_id",
        db_constraint=False,
        related_name="quote_favorites",
    )
    quote = models.ForeignKey(
        Quote,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="favorites",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "quote_favorites"
        constraints = [
            models.UniqueConstraint(fields=["user", "quote"], name="uq_quote_fav_user_quote"),
        ]
        ordering = ["-created_at"]
