from __future__ import annotations

import re

from rest_framework import serializers

from .models import LedgerAccount, LedgerTransaction

# User-chosen tokens: ASCII alphanumeric, exactly 10 chars. Easier to remember
# and type across devices than the previous 43-char server-minted opaque token.
# Backwards compatible at the storage layer — we still SHA-256 it before saving.
TOKEN_RE = re.compile(r"^[A-Za-z0-9]{10}$")


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
    # Optional — perform_create fills in local_today() when missing.
    occurred_on = serializers.DateField(required=False)

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
