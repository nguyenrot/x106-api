"""Studio models — artworks, LLM usage, jobs, request logs.

Each table maps to one already-populated MySQL table; pin Meta.db_table and
keep db_constraint=False on user FKs because the legacy schema dropped them
(charset/collation incompatibility between users.id and the *_user_id columns —
see internal/database/schema.go:107)."""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.core.ids import new_id


# Mode + status string enums match the Go service so existing rows render correctly.
class ArtworkKind(models.TextChoices):
    FAVORITE = "favorite"
    UPLOAD = "upload"
    SNAPSHOT = "snapshot"


class LLMMode(models.TextChoices):
    CHAT = "chat"


class LLMJobStatus(models.TextChoices):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"
    CANCELED = "canceled"


class LLMPromptKind(models.TextChoices):
    CHAT = "chat"
    ROUTER = "router"


class Artwork(models.Model):
    id = models.CharField(primary_key=True, max_length=36, default=new_id, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.DO_NOTHING,
        db_column="user_id",
        db_constraint=False,
        related_name="artworks",
    )
    kind = models.CharField(max_length=24, choices=ArtworkKind.choices, default=ArtworkKind.SNAPSHOT)
    source_id = models.CharField(max_length=80, null=True, blank=True)
    title = models.CharField(max_length=80)
    prompt = models.CharField(max_length=180)
    style = models.CharField(max_length=40)
    palette = models.CharField(max_length=60)
    seed = models.BigIntegerField()
    settings = models.JSONField(db_column="settings_json")
    scene = models.JSONField(db_column="scene_json")
    thumbnail_data_url = models.TextField()  # MEDIUMTEXT in MySQL — left untouched
    asset_data_url = models.TextField(null=True, blank=True)
    # Public share token: presence = artwork is shared via /v/<token>; null = private.
    share_token = models.CharField(max_length=48, null=True, blank=True, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "artworks"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "created_at"], name="idx_artworks_user_created")]


class LLMUsage(models.Model):
    """Daily quota counter, composite primary key (user_id, date).

    `user_id` is a raw VARCHAR(36) column — not a true FK because the legacy
    Go service dropped it for collation reasons. We store as a CharField rather
    than ForeignKey to keep raw upserts simple."""

    user_id = models.CharField(max_length=36)
    date = models.DateField()
    count = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    pk = models.CompositePrimaryKey("user_id", "date")

    class Meta:
        db_table = "llm_usage"


class AppSetting(models.Model):
    """Key/value table — `key` column is reserved in MySQL so we alias the
    Python field as `name`."""

    name = models.CharField(max_length=80, db_column="key", primary_key=True)
    value = models.TextField()  # MEDIUMTEXT in production
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "app_settings"


class LLMJob(models.Model):
    id = models.CharField(primary_key=True, max_length=36, default=new_id, editable=False)
    user_id = models.CharField(max_length=36)
    username = models.CharField(max_length=64, default="")
    mode = models.CharField(max_length=16, choices=LLMMode.choices)
    status = models.CharField(max_length=16, choices=LLMJobStatus.choices, default=LLMJobStatus.PENDING)
    request_body = models.JSONField(null=True, blank=True)
    result_scene = models.JSONField(null=True, blank=True)
    result_message = models.TextField(null=True, blank=True)
    error_message = models.CharField(max_length=500, null=True, blank=True)
    attempt = models.IntegerField(default=0)
    # Per-job model selection. NULL = "resolve admin default when the worker
    # runs". `flash_model` is the intent router, `pro_model` is the scene
    # generator. Stored as catalog ids (e.g. "opencode-go/kimi-k2.6").
    flash_model = models.CharField(max_length=64, null=True, blank=True)
    pro_model = models.CharField(max_length=64, null=True, blank=True)
    # Celery task id captured at enqueue time so admin cancel can revoke(terminate=True).
    celery_task_id = models.CharField(max_length=64, null=True, blank=True)
    # Snapshot of active LLMPromptVersion at job-start time (audit / forensics).
    prompt_version_id = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "llm_jobs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"], name="idx_llm_jobs_status_created"),
            models.Index(fields=["user_id", "created_at"], name="idx_llm_jobs_user_created"),
            models.Index(fields=["prompt_version_id"], name="idx_llm_jobs_prompt_ver"),
        ]


class LLMRequestLog(models.Model):
    """One row per DeepSeek call attempt. The DB column is named
    `parsed_direction` for legacy reasons; its content is now an LLMScene JSON.
    """

    id = models.BigAutoField(primary_key=True)
    user_id = models.CharField(max_length=36)
    username = models.CharField(max_length=64, default="")
    mode = models.CharField(max_length=16)
    model = models.CharField(max_length=64)
    attempt = models.PositiveSmallIntegerField(default=1)
    temperature = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    request_payload = models.JSONField(null=True, blank=True)
    response_raw = models.TextField(null=True, blank=True)
    parsed_direction = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=24)
    error_message = models.TextField(null=True, blank=True)
    latency_ms = models.IntegerField(default=0)
    prompt_tokens = models.IntegerField(default=0)
    completion_tokens = models.IntegerField(default=0)
    total_tokens = models.IntegerField(default=0)
    # HTTP status from the upstream provider (e.g. 200, 429, 500). NULL when the
    # call never made a response (network error, JSON parse before status known).
    http_status = models.SmallIntegerField(null=True, blank=True)
    # Snapshot of LLMPromptVersion in effect when this attempt fired.
    prompt_version_id = models.BigIntegerField(null=True, blank=True)
    # Computed cost in cents (prompt_tokens × rate + completion_tokens × rate).
    # NULL if the model lacks a known rate.
    cost_cents = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "llm_request_logs"
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["created_at"], name="idx_llm_logs_created"),
            models.Index(fields=["user_id", "created_at"], name="idx_llm_logs_user"),
            models.Index(fields=["status", "created_at"], name="idx_llm_logs_status_created"),
            models.Index(fields=["model", "created_at"], name="idx_llm_logs_model_created"),
        ]


class LLMPromptVersion(models.Model):
    """Versioned system prompts for chat (pro) and router (flash) LLM calls.

    Exactly one row per `kind` may have `is_active=True` (partial unique).
    The worker snapshots `prompt_version_id` onto LLMJob + LLMRequestLog so the
    audit trail survives later prompt edits. Fallback path in services/prompts.py
    returns the hardcoded Python constant if no active row exists yet."""

    id = models.BigAutoField(primary_key=True)
    kind = models.CharField(max_length=16, choices=LLMPromptKind.choices)
    body = models.TextField()
    notes = models.CharField(max_length=240, blank=True, default="")
    created_by = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=False)

    class Meta:
        db_table = "llm_prompt_versions"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["kind", "-created_at"], name="idx_promptver_kind_created"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["kind"],
                condition=models.Q(is_active=True),
                name="uq_promptver_active_per_kind",
            ),
        ]
