"""CafeReview — one review per cafe (Đà Nẵng & notable spots).

Single-author blog: only the admin (x106_admin token) writes; everyone reads
published rows. Tags/amenities are JSON arrays of slugs (no M2M table) since
there is exactly one author and filtering is cheap. `content_md` is the source
of truth; `content_html` is rendered server-side on write so the public page
serves ready HTML.
"""

from __future__ import annotations

from django.db import models

from apps.core.ids import new_id


class CafeReview(models.Model):
    id = models.CharField(primary_key=True, max_length=36, default=new_id, editable=False)
    slug = models.SlugField(max_length=200, unique=True)
    name = models.CharField(max_length=200)               # tên quán
    excerpt = models.CharField(max_length=300, blank=True, default="")  # mô tả ngắn cho feed

    # ── Location ──
    address = models.CharField(max_length=300, blank=True, default="")
    district = models.CharField(max_length=80, blank=True, default="")  # quận/huyện
    lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    # ── Practical info ──
    price_level = models.PositiveSmallIntegerField(null=True, blank=True)  # 1..4 ($–$$$$)
    price_note = models.CharField(max_length=120, blank=True, default="")   # "30–60k"
    opening_hours = models.CharField(max_length=200, blank=True, default="")  # "7:00 – 22:00"
    amenities = models.JSONField(default=list, blank=True)  # ["wifi","quiet","work","parking"]
    tags = models.JSONField(default=list, blank=True)        # ["specialty","view-bien","co-dien"]

    # ── Ratings (0–5, .5 steps) ──
    rating_overall = models.DecimalField(max_digits=2, decimal_places=1, null=True, blank=True)
    rating_drink = models.DecimalField(max_digits=2, decimal_places=1, null=True, blank=True)
    rating_space = models.DecimalField(max_digits=2, decimal_places=1, null=True, blank=True)
    rating_price = models.DecimalField(max_digits=2, decimal_places=1, null=True, blank=True)
    rating_service = models.DecimalField(max_digits=2, decimal_places=1, null=True, blank=True)

    # ── Media ──
    cover_image_url = models.URLField(max_length=500, blank=True, default="")
    gallery = models.JSONField(default=list, blank=True)  # list[str] of CDN URLs

    # ── Content ──
    content_md = models.TextField(blank=True, default="")
    content_html = models.TextField(blank=True, default="")

    # ── State ──
    is_published = models.BooleanField(default=False)
    visited_at = models.DateField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "cafe_reviews"
        ordering = ["-published_at", "-created_at"]
        indexes = [
            models.Index(fields=["is_published", "-published_at"], name="idx_cafe_pub"),
        ]

    def __str__(self) -> str:  # pragma: no cover - admin display only
        return self.name


class CafeAgentRun(models.Model):
    """One row per auto-review-agent invocation (mirrors quotes.QuoteAgentRun).

    Write-once audit trail: prompt, raw agy output, parsed JSON, validation
    verdict. Drives the admin "Tạo bài AI" button (client polls the row until
    status leaves `started`) and the runs log.
    """

    STATUS_CHOICES = [
        ("started", "started"),
        ("succeeded", "succeeded"),
        ("skipped", "skipped"),
        ("failed", "failed"),
    ]
    SLOT_CHOICES = [
        ("daily", "daily"),
        ("manual", "manual"),
    ]

    id = models.CharField(primary_key=True, max_length=36, default=new_id, editable=False)
    slot = models.CharField(max_length=16, choices=SLOT_CHOICES, default="manual")
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="started")
    review = models.ForeignKey(
        CafeReview,
        on_delete=models.SET_NULL,
        db_constraint=False,
        null=True,
        blank=True,
        related_name="agent_runs",
    )
    cafe_name = models.CharField(max_length=200, blank=True, default="")
    error_message = models.TextField(blank=True, default="")

    # Full audit trail — written once when the run finishes.
    prompt = models.TextField(blank=True, default="")
    agy_response_raw = models.TextField(blank=True, default="")
    agy_response_parsed = models.JSONField(null=True, blank=True)
    validation_error = models.TextField(blank=True, default="")
    duration_ms = models.IntegerField(null=True, blank=True)
    agy_duration_ms = models.IntegerField(null=True, blank=True)

    class Meta:
        db_table = "cafe_agent_runs"
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["status", "started_at"], name="ix_car_status_started"),
        ]

    def __str__(self) -> str:  # pragma: no cover - admin display only
        return f"{self.slot} {self.status} {self.cafe_name or '—'}"
