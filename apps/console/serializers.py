"""DRF serializers for the console feature.

Read serializers shape what the admin UI polls; write serializers validate
input on `POST /messages`, `POST /execs/.../approve`, etc.
"""

from __future__ import annotations

from rest_framework import serializers

from .models import ConsoleExec, ConsoleMessage, ConsoleSession
from .settings_keys import ALLOWED_MODELS


class ExecSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsoleExec
        fields = (
            "id",
            "session_id",
            "message_id",
            "command",
            "source",
            "status",
            "danger_level",
            "danger_reasons",
            "stdout",
            "stderr",
            "exit_code",
            "latency_ms",
            "error_message",
            "deny_reason",
            "created_at",
            "started_at",
            "finished_at",
        )
        read_only_fields = fields


class MessageSerializer(serializers.ModelSerializer):
    execs = ExecSerializer(many=True, read_only=True)

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
            "execs",
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
    """Body for POST /sessions/{id}/messages — exactly one of
    `content` (NL → AI chat) or `exec_command` (direct shell)."""

    content = serializers.CharField(required=False, allow_blank=False, trim_whitespace=False)
    exec_command = serializers.CharField(required=False, allow_blank=False, trim_whitespace=False)

    def validate(self, attrs):
        has_content = bool(attrs.get("content"))
        has_exec = bool(attrs.get("exec_command"))
        if has_content == has_exec:
            raise serializers.ValidationError(
                "Provide exactly one of `content` or `exec_command`."
            )
        return attrs


class ApproveExecSerializer(serializers.Serializer):
    destroy_phrase = serializers.CharField(required=False, allow_blank=True, default="")


class DenyExecSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class ConsoleSettingsSerializer(serializers.Serializer):
    """Read + write payload for /admin/console/settings."""

    enabled = serializers.BooleanField()
    system_prompt = serializers.CharField(allow_blank=False)
    ai_model = serializers.ChoiceField(choices=ALLOWED_MODELS)
    command_timeout_sec = serializers.IntegerField(min_value=5, max_value=300)
    max_agent_steps = serializers.IntegerField(min_value=1, max_value=20)
    destroy_phrase = serializers.CharField(min_length=3, max_length=64)
