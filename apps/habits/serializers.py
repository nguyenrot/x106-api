from __future__ import annotations

import re

from rest_framework import serializers

from .models import Frequency, Habit, HabitLog, HabitType

TOKEN_RE = re.compile(r"^[A-Za-z0-9]{10}$")


def _normalize_tags(tags: list) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for raw in tags or []:
        if not isinstance(raw, str):
            continue
        t = raw.strip().lower()
        if not t or t in seen:
            continue
        seen.add(t)
        cleaned.append(t)
    return cleaned


def _normalize_weekdays(days: list) -> list[int]:
    out: set[int] = set()
    for d in days or []:
        try:
            n = int(d)
        except (TypeError, ValueError):
            raise serializers.ValidationError("weekdays must be integers 0–6 (0=Mon).") from None
        if not 0 <= n <= 6:
            raise serializers.ValidationError("weekdays must be in 0–6 (0=Mon, 6=Sun).")
        out.add(n)
    return sorted(out)


# ── Accounts (token auth, like ledger) ──────────────────────────────────────

class CreateAccountSerializer(serializers.Serializer):
    token = serializers.CharField()

    def validate_token(self, value: str) -> str:
        if not TOKEN_RE.match(value or ""):
            raise serializers.ValidationError("Token phải đúng 10 ký tự, chỉ chữ và số.")
        return value


# ── Habits ───────────────────────────────────────────────────────────────--

class HabitSerializer(serializers.ModelSerializer):
    """Read + write serializer for habits. Cross-field rules live in validate()."""

    class Meta:
        model = Habit
        fields = [
            "id", "name", "icon", "color",
            "type", "target_count", "unit",
            "frequency", "weekdays", "weekly_target",
            "category", "tags",
            "reminder_enabled", "reminder_time",
            "sort_order", "archived", "archived_at",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "archived_at", "created_at", "updated_at"]

    def validate_tags(self, value):
        return _normalize_tags(value)

    def validate_weekdays(self, value):
        return _normalize_weekdays(value)

    def validate(self, attrs):
        # Merge with the existing instance so PATCH sees the effective values.
        def eff(field, default=None):
            if field in attrs:
                return attrs[field]
            if self.instance is not None:
                return getattr(self.instance, field)
            return default

        htype = eff("type", HabitType.BINARY)
        if htype == HabitType.COUNT:
            target = eff("target_count")
            if not target or target < 1:
                raise serializers.ValidationError(
                    {"target_count": "Đặt mục tiêu ≥ 1 cho thói quen định lượng."}
                )
        else:
            attrs["target_count"] = None
            attrs["unit"] = ""

        freq = eff("frequency", Frequency.DAILY)
        if freq == Frequency.WEEKLY_DAYS:
            weekdays = eff("weekdays") or []
            if not weekdays:
                raise serializers.ValidationError(
                    {"weekdays": "Chọn ít nhất một thứ trong tuần."}
                )
            attrs["weekly_target"] = None
        elif freq == Frequency.WEEKLY_COUNT:
            wt = eff("weekly_target")
            if not wt or not 1 <= wt <= 7:
                raise serializers.ValidationError(
                    {"weekly_target": "Số lần mỗi tuần phải từ 1 đến 7."}
                )
            attrs["weekdays"] = []
        else:  # daily
            attrs["weekdays"] = []
            attrs["weekly_target"] = None

        if eff("reminder_enabled", False) and not eff("reminder_time"):
            raise serializers.ValidationError(
                {"reminder_time": "Bật nhắc nhở thì cần đặt giờ."}
            )

        return attrs


class HabitLogSerializer(serializers.ModelSerializer):
    habit_id = serializers.CharField(read_only=True)

    class Meta:
        model = HabitLog
        fields = ["id", "habit_id", "date", "count", "completed", "note",
                  "created_at", "updated_at"]
        read_only_fields = fields


class UpsertHabitLogSerializer(serializers.Serializer):
    habit = serializers.CharField()
    date = serializers.DateField(required=False, input_formats=["%Y-%m-%d"])
    count = serializers.IntegerField(required=False, min_value=0)
    note = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class ReorderSerializer(serializers.Serializer):
    """Body: {"order": [{"id": "...", "sort_order": 0}, ...]}"""

    order = serializers.ListField(child=serializers.DictField(), allow_empty=False)
