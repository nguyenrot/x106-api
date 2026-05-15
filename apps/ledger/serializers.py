from __future__ import annotations

from rest_framework import serializers

from .models import LedgerAccount, LedgerTransaction


class LedgerAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = LedgerAccount
        fields = ["id", "created_at"]
        read_only_fields = fields


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
