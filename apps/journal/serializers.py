from __future__ import annotations

from rest_framework import serializers

from .models import Vibe


class VibeSerializer(serializers.ModelSerializer):
    user_id = serializers.CharField(read_only=True)

    class Meta:
        model = Vibe
        fields = ["id", "user_id", "date", "mood_emoji", "title", "note", "created_at"]
        read_only_fields = ["id", "user_id", "created_at"]


class UpsertVibeSerializer(serializers.Serializer):
    date = serializers.DateField(required=False, input_formats=["%Y-%m-%d"])
    mood_emoji = serializers.CharField(max_length=10)
    title = serializers.CharField(max_length=255)
    note = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class VibeStatsSerializer(serializers.Serializer):
    total_entries = serializers.IntegerField()
    streak = serializers.IntegerField()
    mood_counts = serializers.DictField(child=serializers.IntegerField())
