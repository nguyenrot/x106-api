from __future__ import annotations

from rest_framework import serializers

from .models import SiteContent


class SiteContentSerializer(serializers.ModelSerializer):
    class Meta:
        model = SiteContent
        fields = ["app", "section", "data", "updated_at"]
        read_only_fields = ["updated_at"]


class UpsertSectionSerializer(serializers.Serializer):
    data = serializers.JSONField()
