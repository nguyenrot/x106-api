"""Artwork + LLM serializers. Validation caps are ported from
internal/handler/artwork.go (cleanText, validateJSONObject, validDataURL)."""

from __future__ import annotations

import json

from rest_framework import serializers

from apps.core.text import clamp_runes

from .models import Artwork, ArtworkKind, LLMJobStatus, LLMMode

MAX_TITLE = 80
MAX_PROMPT = 180
MAX_STYLE = 40
MAX_PALETTE = 60
MAX_KIND = 24
MAX_SOURCE_ID = 80
MAX_SETTINGS_BYTES = 4096
MAX_SCENE_BYTES = 65536
MAX_THUMBNAIL_BYTES = 520_000
MAX_ASSET_BYTES = 900_000

VALID_DATA_URL_PREFIXES = (
    "data:image/webp;base64,",
    "data:image/jpeg;base64,",
)


def _bytes_len(value) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    return len(json.dumps(value).encode("utf-8"))


def _is_json_object(value) -> bool:
    """The Go service rejects JSON arrays/scalars; only objects are stored."""
    return isinstance(value, dict)


def _validate_data_url(value: str | None, max_bytes: int) -> bool:
    if not value:
        return False
    if _bytes_len(value) > max_bytes:
        return False
    return value.startswith(VALID_DATA_URL_PREFIXES)


class ArtworkSerializer(serializers.ModelSerializer):
    user_id = serializers.CharField(read_only=True)

    class Meta:
        model = Artwork
        fields = [
            "id",
            "user_id",
            "kind",
            "source_id",
            "title",
            "prompt",
            "style",
            "palette",
            "seed",
            "settings",
            "scene",
            "thumbnail_data_url",
            "asset_data_url",
            "share_token",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "user_id", "share_token", "created_at", "updated_at"]

    def validate_title(self, value: str) -> str:
        return clamp_runes(value, MAX_TITLE)

    def validate_prompt(self, value: str) -> str:
        return clamp_runes(value or "Digital artwork", MAX_PROMPT)

    def validate_style(self, value: str) -> str:
        return clamp_runes(value or "bold-digital-gallery", MAX_STYLE)

    def validate_palette(self, value: str) -> str:
        return clamp_runes(value or "signal-red", MAX_PALETTE)

    def validate_kind(self, value: str | None) -> str:
        kind = clamp_runes(value or ArtworkKind.SNAPSHOT, MAX_KIND)
        if kind not in {c.value for c in ArtworkKind}:
            raise serializers.ValidationError("kind must be favorite, upload, or snapshot")
        return kind

    def validate_source_id(self, value: str | None) -> str | None:
        if value is None:
            return None
        return clamp_runes(value, MAX_SOURCE_ID) or None

    def validate_settings(self, value):
        if value in (None, ""):
            return {}
        if not _is_json_object(value):
            raise serializers.ValidationError("settings must be a JSON object")
        if _bytes_len(value) > MAX_SETTINGS_BYTES:
            raise serializers.ValidationError("settings payload is too large")
        return value

    def validate_scene(self, value):
        if value in (None, ""):
            return {}
        if not _is_json_object(value):
            raise serializers.ValidationError("scene must be a JSON object")
        if _bytes_len(value) > MAX_SCENE_BYTES:
            raise serializers.ValidationError("scene payload is too large")
        return value

    def validate_thumbnail_data_url(self, value: str) -> str:
        if not _validate_data_url(value, MAX_THUMBNAIL_BYTES):
            raise serializers.ValidationError(
                "thumbnail_data_url must be a small webp or jpeg data URL"
            )
        return value

    def validate_asset_data_url(self, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        if not _validate_data_url(cleaned, MAX_ASSET_BYTES):
            raise serializers.ValidationError(
                "asset_data_url must be a small webp or jpeg data URL"
            )
        return cleaned

    def validate(self, attrs):
        # Title defaults to prompt when blank — matches the Go fallback.
        if not attrs.get("title"):
            attrs["title"] = attrs.get("prompt") or "Digital artwork"
        return attrs


class PublicArtworkSerializer(serializers.ModelSerializer):
    """Read-only shape returned by /api/v1/public/artworks/<token>.

    Strips user_id and storage-only fields; exposes owner_username so the viewer
    UI can attribute the snapshot."""

    owner_username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = Artwork
        fields = [
            "id",
            "title",
            "scene",
            "thumbnail_data_url",
            "created_at",
            "owner_username",
        ]
        read_only_fields = fields


# ─── LLM ──────────────────────────────────────────────────────────────────


class LLMQuotaSerializer(serializers.Serializer):
    used = serializers.IntegerField()
    remaining = serializers.IntegerField()
    limit = serializers.IntegerField()


class ChatTurnSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=[("user", "user"), ("assistant", "assistant")])
    content = serializers.CharField(max_length=400, allow_blank=False)


class LLMSubmitSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(choices=LLMMode.choices, default=LLMMode.CHAT)
    currentScene = serializers.JSONField(required=False, allow_null=True)
    userMessage = serializers.CharField(
        required=True, allow_blank=False, max_length=400
    )
    history = serializers.ListField(
        required=False, child=ChatTurnSerializer(), max_length=4
    )


class LLMJobSubmitResponseSerializer(serializers.Serializer):
    jobId = serializers.CharField()
    used = serializers.IntegerField()
    remaining = serializers.IntegerField()
    limit = serializers.IntegerField()


class LLMJobStatusResponseSerializer(serializers.Serializer):
    jobId = serializers.CharField()
    status = serializers.ChoiceField(choices=LLMJobStatus.choices)
    mode = serializers.ChoiceField(choices=LLMMode.choices)
    scene = serializers.JSONField(required=False, allow_null=True)
    assistantMessage = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    errorMessage = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    elapsedMs = serializers.IntegerField()
    used = serializers.IntegerField()
    remaining = serializers.IntegerField()
    limit = serializers.IntegerField()
