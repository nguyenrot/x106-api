from __future__ import annotations

import re
from typing import Optional

from django.utils.text import slugify
from rest_framework import serializers

from .models import LedgerAccount, LedgerCategoryRow, LedgerKind, LedgerTransaction

# User-chosen tokens: ASCII alphanumeric, exactly 10 chars. Easier to remember
# and type across devices than the previous 43-char server-minted opaque token.
# Backwards compatible at the storage layer — we still SHA-256 it before saving.
TOKEN_RE = re.compile(r"^[A-Za-z0-9]{10}$")
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class LedgerAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = LedgerAccount
        fields = ["id", "created_at"]
        read_only_fields = fields


class CreateAccountSerializer(serializers.Serializer):
    token = serializers.CharField(trim_whitespace=False)

    def validate_token(self, value: str) -> str:
        if not TOKEN_RE.match(value or ""):
            raise serializers.ValidationError(
                "Token phải đúng 10 ký tự, chỉ gồm chữ cái (A-Z, a-z) và chữ số."
            )
        return value


class LedgerTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = LedgerTransaction
        fields = ["id", "kind", "amount", "category", "note", "occurred_on", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate_amount(self, value: int) -> int:
        if value <= 0:
            raise serializers.ValidationError("Số tiền phải là số nguyên dương (VND).")
        # 10^12 đ ≈ 1.000 tỷ — đủ rộng cho mọi nhu cầu cá nhân, lọc input rác.
        if value > 10**12:
            raise serializers.ValidationError("Số tiền quá lớn.")
        return value

    def validate_note(self, value: str | None) -> str:
        v = (value or "").strip()
        if len(v) > 255:
            raise serializers.ValidationError("Ghi chú tối đa 255 ký tự.")
        return v

    def validate_category(self, value: str) -> str:
        v = (value or "").strip()
        if not v:
            raise serializers.ValidationError("Phân loại không được để trống.")
        if len(v) > 64:
            raise serializers.ValidationError("Phân loại tối đa 64 ký tự.")
        return v


# ── Category CRUD ─────────────────────────────────────────────────────────


class LedgerCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = LedgerCategoryRow
        fields = ["id", "kind", "slug", "name", "color", "position", "is_archived", "created_at"]
        read_only_fields = ["id", "slug", "is_archived", "created_at"]


def _make_unique_slug(account: LedgerAccount, kind: str, name: str, exclude_id: Optional[str] = None) -> str:
    """Slugify the name then append `-N` until unique within (account, kind).

    Vietnamese diacritics are stripped by Django's slugify (NFKD then strip
    combining marks), so "Du lịch" → "du-lich".
    """
    base = slugify(name) or "category"
    candidate = base
    n = 2
    while True:
        qs = LedgerCategoryRow.objects.filter(account=account, kind=kind, slug=candidate)
        if exclude_id:
            qs = qs.exclude(id=exclude_id)
        if not qs.exists():
            return candidate
        candidate = f"{base}-{n}"
        n += 1


class CategoryCreateSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=LedgerKind.choices)
    name = serializers.CharField(max_length=40)
    color = serializers.CharField(max_length=7, required=False, default="#94a3b8")
    position = serializers.IntegerField(required=False, min_value=0)

    def validate_name(self, value: str) -> str:
        v = (value or "").strip()
        if not v:
            raise serializers.ValidationError("Tên danh mục không được để trống.")
        return v

    def validate_color(self, value: str) -> str:
        v = (value or "").strip()
        if not HEX_COLOR_RE.match(v):
            raise serializers.ValidationError("Màu phải ở dạng #RRGGBB.")
        return v.lower()


class CategoryUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=40, required=False)
    color = serializers.CharField(max_length=7, required=False)
    position = serializers.IntegerField(required=False, min_value=0)

    def validate_name(self, value: str) -> str:
        v = (value or "").strip()
        if not v:
            raise serializers.ValidationError("Tên danh mục không được để trống.")
        return v

    def validate_color(self, value: str) -> str:
        v = (value or "").strip()
        if not HEX_COLOR_RE.match(v):
            raise serializers.ValidationError("Màu phải ở dạng #RRGGBB.")
        return v.lower()


class CategoryReorderSerializer(serializers.Serializer):
    """Bulk position update — frontend sends [{id, position}, ...]."""
    kind = serializers.ChoiceField(choices=LedgerKind.choices)
    order = serializers.ListField(child=serializers.CharField(max_length=36))
