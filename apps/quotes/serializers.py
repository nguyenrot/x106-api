from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers

from .models import Baogia, BaogiaLineItem, Quote, QuoteFavorite


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


class BaogiaLineItemSerializer(serializers.ModelSerializer):
    line_total = serializers.SerializerMethodField()

    class Meta:
        model = BaogiaLineItem
        fields = ["id", "description", "quantity", "unit_price", "sort_order", "line_total"]
        read_only_fields = ["id", "line_total"]

    def get_line_total(self, obj) -> str:
        total = Decimal(obj.quantity) * Decimal(obj.unit_price)
        return f"{total:.2f}"


class BaogiaSerializer(serializers.ModelSerializer):
    items = BaogiaLineItemSerializer(many=True, read_only=True)
    total = serializers.SerializerMethodField()

    class Meta:
        model = Baogia
        fields = [
            "id",
            "share_token",
            "client_name",
            "client_company",
            "title",
            "notes",
            "currency",
            "valid_until",
            "issued_at",
            "created_at",
            "updated_at",
            "items",
            "total",
        ]
        read_only_fields = ["id", "share_token", "issued_at", "created_at", "updated_at", "items", "total"]

    def get_total(self, obj) -> str:
        total = sum((Decimal(i.quantity) * Decimal(i.unit_price) for i in obj.items.all()), Decimal(0))
        return f"{total:.2f}"


class UpsertBaogiaSerializer(serializers.Serializer):
    client_name = serializers.CharField(max_length=200)
    client_company = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    title = serializers.CharField(max_length=200)
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    currency = serializers.CharField(max_length=8, required=False, default="VND")
    valid_until = serializers.DateField(required=False, allow_null=True)


class UpsertLineItemSerializer(serializers.Serializer):
    description = serializers.CharField(max_length=500)
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2, default=Decimal(1))
    unit_price = serializers.DecimalField(max_digits=14, decimal_places=2, default=Decimal(0))
    sort_order = serializers.IntegerField(default=0)
