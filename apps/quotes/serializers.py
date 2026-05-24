from __future__ import annotations

from rest_framework import serializers

from .models import Quote, QuoteFavorite


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


class QuoteSerializer(serializers.ModelSerializer):
    user_id = serializers.CharField(read_only=True, allow_null=True)
    favorited = serializers.SerializerMethodField()

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

    def get_favorited(self, obj) -> bool:
        request = self.context.get("request")
        if not request or not request.user or not request.user.is_authenticated:
            return False
        fav_ids = self.context.get("favorited_ids")
        if fav_ids is not None:
            return obj.id in fav_ids
        return QuoteFavorite.objects.filter(user=request.user, quote=obj).exists()


class UpsertQuoteSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=4000)
    author = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    source = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    tags = serializers.ListField(
        child=serializers.CharField(max_length=32),
        required=False,
        default=list,
    )
    language = serializers.ChoiceField(choices=["vi", "en"], default="vi")
    is_public = serializers.BooleanField(required=False, default=False)

    def validate_body(self, value: str) -> str:
        v = value.strip()
        if not v:
            raise serializers.ValidationError("Quote không được để trống.")
        return v

    def validate_tags(self, value: list[str]) -> list[str]:
        return _normalize_tags(value)
