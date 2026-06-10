from django.contrib import admin

from .models import CafeAgentRun, CafeReview


@admin.register(CafeReview)
class CafeReviewAdmin(admin.ModelAdmin):
    list_display = ("name", "district", "rating_overall", "is_published", "published_at", "updated_at")
    list_filter = ("is_published", "district")
    search_fields = ("name", "address", "excerpt")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("content_html", "created_at", "updated_at")


@admin.register(CafeAgentRun)
class CafeAgentRunAdmin(admin.ModelAdmin):
    """Read-only viewer for the agent audit trail (full agy transcripts)."""

    list_display = ("started_at", "slot", "status", "cafe_name", "agy_duration_ms", "duration_ms")
    list_filter = ("status", "slot")
    search_fields = ("cafe_name", "error_message")
    readonly_fields = [f.name for f in CafeAgentRun._meta.fields]

    def has_add_permission(self, request):  # pragma: no cover - admin plumbing
        return False

    def has_change_permission(self, request, obj=None):  # pragma: no cover
        return False
