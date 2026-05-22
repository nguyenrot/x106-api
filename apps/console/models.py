"""VPS console + AI ops assistant models.

Four tables:
- console_settings: key/value config (replaces the old `app_settings` table).
- console_sessions: a chat conversation.
- console_messages: each turn within a session (user / assistant / system).
- console_execs: a single shell command run + result. Linked to a message if
  the AI proposed it; standalone if the user typed it with the `$ ` prefix.
  Doubles as the audit log — every command ever run lives here.
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

    # Lifecycle for assistant messages driven by the agent loop:
    #   pending → streaming → awaiting_confirm ↔ streaming → done|failed|canceled
    # User messages skip straight to `done`.
    STATUS_PENDING = "pending"
    STATUS_STREAMING = "streaming"
    STATUS_AWAITING_CONFIRM = "awaiting_confirm"
    STATUS_DONE = "done"
    STATUS_FAILED = "failed"
    STATUS_CANCELED = "canceled"
    STATUS_CHOICES = [
        (STATUS_PENDING, "pending"),
        (STATUS_STREAMING, "streaming"),
        (STATUS_AWAITING_CONFIRM, "awaiting_confirm"),
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


class ConsoleExec(models.Model):
    SOURCE_USER_DIRECT = "user_direct"
    SOURCE_AI_PROPOSED = "ai_proposed"
    SOURCE_CHOICES = [
        (SOURCE_USER_DIRECT, "user_direct"),
        (SOURCE_AI_PROPOSED, "ai_proposed"),
    ]

    # Lifecycle:
    #   awaiting_confirm → approved → running → done|failed
    #   awaiting_confirm → denied|canceled
    #   running → canceled
    STATUS_AWAITING_CONFIRM = "awaiting_confirm"
    STATUS_APPROVED = "approved"
    STATUS_RUNNING = "running"
    STATUS_DONE = "done"
    STATUS_FAILED = "failed"
    STATUS_CANCELED = "canceled"
    STATUS_DENIED = "denied"
    STATUS_CHOICES = [
        (STATUS_AWAITING_CONFIRM, "awaiting_confirm"),
        (STATUS_APPROVED, "approved"),
        (STATUS_RUNNING, "running"),
        (STATUS_DONE, "done"),
        (STATUS_FAILED, "failed"),
        (STATUS_CANCELED, "canceled"),
        (STATUS_DENIED, "denied"),
    ]

    DANGER_SAFE = "safe"
    DANGER_WRITE = "write"
    DANGER_DESTRUCTIVE = "destructive"
    DANGER_CHOICES = [
        (DANGER_SAFE, "safe"),
        (DANGER_WRITE, "write"),
        (DANGER_DESTRUCTIVE, "destructive"),
    ]

    id = models.CharField(primary_key=True, max_length=36, default=new_id, editable=False)
    session = models.ForeignKey(
        ConsoleSession,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="execs",
        db_column="session_id",
    )
    message = models.ForeignKey(
        ConsoleMessage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="execs",
        db_column="message_id",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.DO_NOTHING,
        db_column="user_id",
        db_constraint=False,
        related_name="console_execs",
    )
    command = models.TextField()
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_AWAITING_CONFIRM)
    danger_level = models.CharField(max_length=16, choices=DANGER_CHOICES, default=DANGER_WRITE)
    danger_reasons = models.JSONField(default=list, blank=True)
    stdout = models.TextField(blank=True, default="")
    stderr = models.TextField(blank=True, default="")
    exit_code = models.IntegerField(null=True, blank=True)
    latency_ms = models.IntegerField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")
    # Open tool_call_id from the LLM, so we can return the matching tool_result
    # in the next chat completion. Null for user-typed direct commands.
    tool_call_id = models.CharField(max_length=128, blank=True, default="")
    deny_reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "console_execs"
        indexes = [
            models.Index(fields=["user", "-created_at"], name="ce_user_created_idx"),
            models.Index(fields=["session", "created_at"], name="ce_session_created_idx"),
            models.Index(fields=["status", "started_at"], name="ce_status_started_idx"),
        ]
