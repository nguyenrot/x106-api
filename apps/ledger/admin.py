from django.contrib import admin

from .models import LedgerAccount, LedgerTransaction


@admin.register(LedgerAccount)
class LedgerAccountAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "updated_at")
    readonly_fields = ("id", "token_hash", "created_at", "updated_at")
    search_fields = ("id",)
    ordering = ("-created_at",)


@admin.register(LedgerTransaction)
class LedgerTransactionAdmin(admin.ModelAdmin):
    list_display = ("id", "account", "kind", "amount", "category", "occurred_on", "created_at")
    list_filter = ("kind", "category", "occurred_on")
    search_fields = ("note", "account__id")
    ordering = ("-occurred_on", "-created_at")
