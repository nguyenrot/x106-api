"""DRF serializers for the console feature.

The console now drives agy autonomously per chat turn — there are no
ConsoleExec rows, no approval flow, and no model allowlist to validate.
"""

from __future__ import annotations

from rest_framework import serializers

from .models import ConsoleMessage, ConsoleSession


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsoleMessage
        fields = (
            "id",
            "session_id",
            "role",
            "content",
            "step_count",
            "status",
            "error_message",
            "created_at",
        )
        read_only_fields = fields


class SessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsoleSession
        fields = ("id", "title", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")


class SessionDetailSerializer(SessionSerializer):
    messages = MessageSerializer(many=True, read_only=True)

    class Meta(SessionSerializer.Meta):
        fields = SessionSerializer.Meta.fields + ("messages",)


class SendMessageSerializer(serializers.Serializer):
    """Body for POST /sessions/{id}/messages — only natural-language chat
    now. Direct shell exec was removed when agy took over (agy decides what
    shell command to run on its own)."""

    content = serializers.CharField(allow_blank=False, trim_whitespace=False)


class ConsoleSettingsSerializer(serializers.Serializer):
    """Read + write payload for /admin/console/settings — only two knobs
    matter now."""

    enabled = serializers.BooleanField()
    system_prompt = serializers.CharField(allow_blank=False)
