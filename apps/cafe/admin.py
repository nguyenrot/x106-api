from django.contrib import admin

from .models import CafeReview


@admin.register(CafeReview)
class CafeReviewAdmin(admin.ModelAdmin):
    list_display = ("name", "district", "rating_overall", "is_published", "published_at", "updated_at")
    list_filter = ("is_published", "district")
    search_fields = ("name", "address", "excerpt")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("content_html", "created_at", "updated_at")
