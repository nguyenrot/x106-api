from __future__ import annotations

import json

from rest_framework import serializers

from .models import GameSave

# A Vấn Đạo save is a few hundred bytes of counters. The ceiling is generous enough to
# absorb years of schema growth while keeping a shared MySQL out of reach of a client
# that decides to store something silly.
MAX_SAVE_BYTES = 64 * 1024


class GameSaveSerializer(serializers.ModelSerializer):
    updatedAt = serializers.DateTimeField(source="updated_at", read_only=True)

    class Meta:
        model = GameSave
        fields = ["data", "revision", "updatedAt"]
        read_only_fields = fields


class PutSaveSerializer(serializers.Serializer):
    data = serializers.JSONField()
    # The revision the client last synced; 0 means "I have never synced". A mismatch
    # against the stored revision means another device moved ahead.
    baseRevision = serializers.IntegerField(min_value=0)
    # Set when the player explicitly chose this device's copy in the conflict prompt.
    force = serializers.BooleanField(default=False)

    def validate_data(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("save must be a JSON object")
        if len(json.dumps(value, separators=(",", ":"))) > MAX_SAVE_BYTES:
            raise serializers.ValidationError("save is too large")
        return value
