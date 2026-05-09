from django.contrib import admin

from .models import Artwork, LLMJob, LLMRequestLog


@admin.register(Artwork)
class ArtworkAdmin(admin.ModelAdmin):
    list_display = ("id", "user_id", "kind", "title", "created_at")
    list_filter = ("kind",)
    search_fields = ("title", "prompt", "user_id")
    readonly_fields = ("created_at", "updated_at")


@admin.register(LLMJob)
class LLMJobAdmin(admin.ModelAdmin):
    list_display = ("id", "user_id", "mode", "status", "created_at", "finished_at")
    list_filter = ("status", "mode")
    search_fields = ("id", "user_id", "username")
    readonly_fields = ("created_at", "started_at", "finished_at")


@admin.register(LLMRequestLog)
class LLMRequestLogAdmin(admin.ModelAdmin):
    list_display = ("id", "user_id", "mode", "model", "status", "latency_ms", "created_at")
    list_filter = ("status", "mode", "model")
    search_fields = ("user_id", "username")
    readonly_fields = tuple(f.name for f in LLMRequestLog._meta.fields)
