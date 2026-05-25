from __future__ import annotations

from rest_framework import serializers

from .models import Quote, QuoteAgentRun, QuoteFavorite


def _normalize_tags(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in tags or []:
        if not isinstance(raw, str):
            continue
        t = raw.strip().lower()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out[:12]


def _normalize_body(value) -> dict:
    """Coerce any of (str, {en,vi} dict, None) into a canonical {en, vi} dict.
    At least one of en/vi must be non-empty — caller raises if not."""

    if value is None:
        return {"en": "", "vi": ""}
    if isinstance(value, str):
        return {"en": "", "vi": value.strip()}
    if isinstance(value, dict):
        return {
            "en": (value.get("en") or "").strip(),
            "vi": (value.get("vi") or "").strip(),
        }
    raise serializers.ValidationError("body must be a string or {en, vi} object.")


class QuoteSerializer(serializers.ModelSerializer):
    user_id = serializers.CharField(read_only=True, allow_null=True)
    favorited = serializers.SerializerMethodField()
    body = serializers.SerializerMethodField()

    class Meta:
        model = Quote
        fields = [
            "id",
            "user_id",
            "body",
            "author",
            "source",
            "tags",
            "language",
            "is_public",
            "is_curated",
            "is_featured",
            "favorited",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "user_id",
            "is_curated",
            "is_featured",
            "favorited",
            "created_at",
            "updated_at",
        ]

    def get_body(self, obj) -> dict:
        return _normalize_body(obj.body)

    def get_favorited(self, obj) -> bool:
        request = self.context.get("request")
        if not request or not request.user or not request.user.is_authenticated:
            return False
        fav_ids = self.context.get("favorited_ids")
        if fav_ids is not None:
            return obj.id in fav_ids
        return QuoteFavorite.objects.filter(user=request.user, quote=obj).exists()


class UpsertQuoteSerializer(serializers.Serializer):
    body = serializers.JSONField()
    author = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    source = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    tags = serializers.ListField(
        child=serializers.CharField(max_length=32),
        required=False,
        default=list,
    )
    language = serializers.ChoiceField(choices=["vi", "en"], default="vi")
    is_public = serializers.BooleanField(required=False, default=False)

    def validate_body(self, value) -> dict:
        body = _normalize_body(value)
        if not body["en"] and not body["vi"]:
            raise serializers.ValidationError("Quote không được để trống (cần ít nhất 1 ngôn ngữ).")
        return body

    def validate_tags(self, value: list[str]) -> list[str]:
        return _normalize_tags(value)


class AdminUpsertQuoteSerializer(UpsertQuoteSerializer):
    """Admin / service variant — can set is_curated and is_featured directly.

    Defaults all three flags to True so a service-token POST with only
    {body, author, source} ships a fully-published, featurable quote in one
    call (matches the agent's daily flow).
    """

    is_curated = serializers.BooleanField(required=False, default=True)
    is_featured = serializers.BooleanField(required=False, default=False)
    # Re-declare is_public to flip its default from False (user submit) to True.
    is_public = serializers.BooleanField(required=False, default=True)


class AgentRunSerializer(serializers.ModelSerializer):
    """Full serializer — POST /agent-runs (write) and GET detail (read).

    For list views use `AgentRunListSerializer` instead — it skips the heavy
    text columns to keep the response small."""

    quote_id = serializers.CharField(required=False, allow_null=True, allow_blank=True)

    class Meta:
        model = QuoteAgentRun
        fields = [
            "id",
            "service_name",
            "started_at",
            "ended_at",
            "status",
            "theme_slug",
            "quote_id",
            "error_message",
            "extras",
            "prompt",
            "agy_response_raw",
            "agy_response_parsed",
            "validation_error",
            "duration_ms",
            "agy_duration_ms",
        ]
        read_only_fields = ["id", "started_at"]

    def validate_status(self, v):
        allowed = {"started", "succeeded", "skipped", "duplicate", "failed"}
        if v not in allowed:
            raise serializers.ValidationError(f"status must be one of {sorted(allowed)}")
        return v

    def create(self, validated_data):
        # Default service_name from auth context if caller didn't pass one.
        if not validated_data.get("service_name"):
            ctx_default = (self.context or {}).get("default_service") or "quotes-agent"
            validated_data["service_name"] = ctx_default
        quote_id = validated_data.pop("quote_id", None) or None
        if quote_id:
            validated_data["quote_id"] = quote_id
        return QuoteAgentRun.objects.create(**validated_data)


class AgentRunListSerializer(serializers.ModelSerializer):
    """Lightweight version for the list view — omits prompt/response text."""

    quote_id = serializers.CharField(allow_null=True)
    prompt_length = serializers.SerializerMethodField()
    response_length = serializers.SerializerMethodField()

    class Meta:
        model = QuoteAgentRun
        fields = [
            "id",
            "service_name",
            "started_at",
            "ended_at",
            "status",
            "theme_slug",
            "quote_id",
            "duration_ms",
            "agy_duration_ms",
            "prompt_length",
            "response_length",
            "error_message",
        ]

    def get_prompt_length(self, obj) -> int:
        return len(obj.prompt or "")

    def get_response_length(self, obj) -> int:
        return len(obj.agy_response_raw or "")
