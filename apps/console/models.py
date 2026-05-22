"""VPS console + AI ops assistant models.

Two tables:
- console_settings: key/value config (enabled + system_prompt).
- console_sessions: a chat conversation.
- console_messages: each turn within a session (user / assistant).

The agy CLI handles shell execution autonomously inside its own process —
no ConsoleExec audit row is created on our side. agy itself persists
conversation/tool traces under `~/.gemini/antigravity-cli/conversations/`.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.core.ids import new_id


class ConsoleSetting(models.Model):
    key = models.CharField(primary_key=True, max_length=64)
    value = models.TextField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "console_settings"


class ConsoleSession(models.Model):
    id = models.CharField(primary_key=True, max_length=36, default=new_id, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.DO_NOTHING,
        db_column="user_id",
        db_constraint=False,
        related_name="console_sessions",
    )
    title = models.CharField(max_length=255, default="Cuộc trò chuyện mới")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "console_sessions"
        indexes = [
            models.Index(fields=["user", "-updated_at"], name="cs_user_updated_idx"),
        ]


class ConsoleMessage(models.Model):
    ROLE_USER = "user"
    ROLE_ASSISTANT = "assistant"
    ROLE_SYSTEM = "system"
    ROLE_CHOICES = [
        (ROLE_USER, "user"),
        (ROLE_ASSISTANT, "assistant"),
        (ROLE_SYSTEM, "system"),
    ]

    # Assistant message lifecycle: pending → streaming → done|failed|canceled.
    # User messages skip straight to `done`.
    STATUS_PENDING = "pending"
    STATUS_STREAMING = "streaming"
    STATUS_DONE = "done"
    STATUS_FAILED = "failed"
    STATUS_CANCELED = "canceled"
    STATUS_CHOICES = [
        (STATUS_PENDING, "pending"),
        (STATUS_STREAMING, "streaming"),
        (STATUS_DONE, "done"),
        (STATUS_FAILED, "failed"),
        (STATUS_CANCELED, "canceled"),
    ]

    id = models.CharField(primary_key=True, max_length=36, default=new_id, editable=False)
    session = models.ForeignKey(
        ConsoleSession,
        on_delete=models.CASCADE,
        related_name="messages",
        db_column="session_id",
    )
    role = models.CharField(max_length=16, choices=ROLE_CHOICES)
    content = models.TextField(blank=True, default="")
    step_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DONE)
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "console_messages"
        indexes = [
            models.Index(fields=["session", "created_at"], name="cm_session_created_idx"),
        ]
