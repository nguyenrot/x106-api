from __future__ import annotations

from rest_framework import serializers

from .models import Vibe


def _normalize_tags(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for raw in tags:
        if not isinstance(raw, str):
            continue
        t = raw.strip().lower()
        if not t or t in seen:
            continue
        seen.add(t)
        cleaned.append(t)
    return cleaned


class VibeSerializer(serializers.ModelSerializer):
    user_id = serializers.CharField(read_only=True)

    class Meta:
        model = Vibe
        fields = ["id", "user_id", "date", "mood_emoji", "title", "note", "tags", "created_at"]
        read_only_fields = ["id", "user_id", "created_at"]


class UpsertVibeSerializer(serializers.Serializer):
    date = serializers.DateField(required=False, input_formats=["%Y-%m-%d"])
    mood_emoji = serializers.CharField(max_length=10)
    title = serializers.CharField(max_length=255)
    note = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    tags = serializers.ListField(
        child=serializers.CharField(max_length=32, allow_blank=False),
        max_length=10,
        required=False,
        default=list,
    )

    def validate_tags(self, value: list[str]) -> list[str]:
        return _normalize_tags(value)


class VibeStatsSerializer(serializers.Serializer):
    total_entries = serializers.IntegerField()
    streak = serializers.IntegerField()
    mood_counts = serializers.DictField(child=serializers.IntegerField())


class ApplyFreezeSerializer(serializers.Serializer):
    date = serializers.DateField(input_formats=["%Y-%m-%d"])
