from __future__ import annotations

from rest_framework import serializers

from apps.studio.settings_keys import ALLOWED_LLM_MODELS


class ArtSetQuotaSerializer(serializers.Serializer):
    count = serializers.IntegerField(min_value=0)


class ArtAdjustQuotaSerializer(serializers.Serializer):
    delta = serializers.IntegerField()


class ArtPromptUpdateSerializer(serializers.Serializer):
    prompt = serializers.CharField(allow_blank=True, allow_null=True)


class ArtSettingsUpdateSerializer(serializers.Serializer):
    dailyLimit = serializers.IntegerField(required=False, min_value=0, max_value=10_000)
    enabled = serializers.BooleanField(required=False)
    model = serializers.ChoiceField(required=False, choices=ALLOWED_LLM_MODELS)
