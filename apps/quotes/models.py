"""Quote + QuoteFavorite + QuoteAgentRun — personal quote library shared via
quotes.kynguyen.cc.

`Quote.body` is a bilingual dict — `{"en": "...", "vi": "..."}`. The
`language` field marks the *primary/original* language (used for filtering,
and as the fallback when the user has selected a UI language with no matching
translation).

`Quote.dedup_hash` is auto-computed in save() — SHA-256 over the normalized
English body + author. Migration 0004 adds a conditional UNIQUE constraint on
it so the daily agent can post idempotently (a second insert of the same
quote raises IntegrityError; AdminQuoteViewSet.create catches it and returns
409 with the existing row's id).

`QuoteAgentRun` is the audit trail / healthcheck source for the daily agent.
"""

from __future__ import annotations

import hashlib
import re
from secrets import token_urlsafe

from django.conf import settings
from django.db import models

from apps.core.ids import new_id


def _new_share_token() -> str:
    """Kept for migration 0001_initial which referenced this default.
    The Baogia/BaogiaLineItem models that used it were dropped in 0002."""
    return token_urlsafe(24)


def _empty_body() -> dict:
    return {"en": "", "vi": ""}


_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize_for_hash(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace. Used by dedup_hash."""
    if not text:
        return ""
    s = text.casefold()
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def compute_dedup_hash(body: dict | str | None, author: str) -> str | None:
    """SHA-256 over normalized(en or vi) + '|' + normalized(author).

    Returns None when there is no usable text — caller should leave the
    column NULL (the partial unique constraint ignores NULLs)."""
    if isinstance(body, dict):
        text = (body.get("en") or "").strip() or (body.get("vi") or "").strip()
    elif isinstance(body, str):
        text = body
    else:
        text = ""
    text_n = normalize_for_hash(text)
    if not text_n:
        return None
    author_n = normalize_for_hash(author or "")
    payload = f"{text_n}|{author_n}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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
    body = models.JSONField(default=_empty_body)
    author = models.CharField(max_length=200, blank=True, default="")
    source = models.CharField(max_length=500, blank=True, default="")
    tags = models.JSONField(default=list, blank=True)
    language = models.CharField(max_length=8, default="vi")
    is_public = models.BooleanField(default=False)
    is_curated = models.BooleanField(default=False)
    is_featured = models.BooleanField(default=False)
    dedup_hash = models.CharField(max_length=64, null=True, blank=True, db_index=True)
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
        constraints = [
            models.UniqueConstraint(
                fields=["dedup_hash"],
                name="uq_quotes_dedup_hash",
                condition=models.Q(dedup_hash__isnull=False),
            ),
        ]

    def save(self, *args, **kwargs):
        self.dedup_hash = compute_dedup_hash(self.body, self.author)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "dedup_hash" not in update_fields:
            kwargs["update_fields"] = list(update_fields) + ["dedup_hash"]
        return super().save(*args, **kwargs)


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


class QuoteAgentRun(models.Model):
    """One row per daily-agent invocation. Drives /admin/quotes/agent-status
    + the admin UI's agent-runs log viewer.

    Logs everything observable per run: the prompt the LLM saw, the raw text
    it spat back, the parsed JSON, and what the local validator said. This is
    a write-once audit trail — no UPDATE after the agent finishes a run.
    """

    STATUS_CHOICES = [
        ("started", "started"),
        ("succeeded", "succeeded"),
        ("skipped", "skipped"),
        ("duplicate", "duplicate"),
        ("failed", "failed"),
    ]

    id = models.CharField(primary_key=True, max_length=36, default=new_id, editable=False)
    service_name = models.CharField(max_length=64, default="quotes-agent", db_index=True)
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="started")
    theme_slug = models.CharField(max_length=64, blank=True, default="")
    quote = models.ForeignKey(
        Quote,
        on_delete=models.SET_NULL,
        db_constraint=False,
        null=True,
        blank=True,
        related_name="agent_runs",
    )
    error_message = models.TextField(blank=True, default="")
    extras = models.JSONField(default=dict, blank=True)

    # Full audit trail — agent writes these once per run, admin UI reads them.
    prompt = models.TextField(blank=True, default="")
    agy_response_raw = models.TextField(blank=True, default="")
    agy_response_parsed = models.JSONField(null=True, blank=True)
    validation_error = models.TextField(blank=True, default="")
    duration_ms = models.IntegerField(null=True, blank=True)
    agy_duration_ms = models.IntegerField(null=True, blank=True)

    class Meta:
        db_table = "quote_agent_runs"
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["status", "started_at"], name="ix_qar_status_started"),
        ]
