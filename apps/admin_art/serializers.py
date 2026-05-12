from __future__ import annotations

from rest_framework import serializers

from apps.studio.services.model_catalog import (
    is_known_model,
    supports_role,
)
from apps.studio.settings_keys import ALLOWED_LLM_MODELS


class ArtSetQuotaSerializer(serializers.Serializer):
    count = serializers.IntegerField(min_value=0)


class ArtAdjustQuotaSerializer(serializers.Serializer):
    delta = serializers.IntegerField()


def _validate_model_with_role(value: str, role: str) -> str:
    if not value:
        return value
    if not is_known_model(value):
        raise serializers.ValidationError(f"unknown model: {value}")
    if not supports_role(value, role):
        raise serializers.ValidationError(
            f"model {value} does not support role={role}"
        )
    return value


class _ModelListField(serializers.ListField):
    """ListField of catalog model ids, optionally constrained by role."""

    def __init__(self, *args, role: str | None = None, **kwargs):
        self._role = role
        super().__init__(
            *args,
            child=serializers.CharField(max_length=64, allow_blank=False),
            allow_empty=True,
            **kwargs,
        )

    def to_internal_value(self, data):
        items = super().to_internal_value(data)
        cleaned: list[str] = []
        for v in items:
            if not is_known_model(v):
                raise serializers.ValidationError(f"unknown model: {v}")
            if self._role and not supports_role(v, self._role):
                raise serializers.ValidationError(
                    f"model {v} does not support role={self._role}"
                )
            cleaned.append(v)
        # Deduplicate while preserving order.
        seen: set[str] = set()
        deduped: list[str] = []
        for v in cleaned:
            if v in seen:
                continue
            seen.add(v)
            deduped.append(v)
        return deduped


class ArtSettingsUpdateSerializer(serializers.Serializer):
    dailyLimit = serializers.IntegerField(required=False, min_value=0, max_value=10_000)
    enabled = serializers.BooleanField(required=False)
    # Legacy single-field write — kept so old admin clients still apply.
    model = serializers.ChoiceField(required=False, choices=ALLOWED_LLM_MODELS)
    # New: per-role defaults + allow-lists.
    flashModel = serializers.CharField(required=False, allow_blank=False, max_length=64)
    proModel = serializers.CharField(required=False, allow_blank=False, max_length=64)
    allowedFlashModels = _ModelListField(required=False, role="flash")
    allowedProModels = _ModelListField(required=False, role="pro")
    # Phase 1.5 right-size: per-call token budget. Pro 256..32000, flash 64..2000.
    proMaxTokens = serializers.IntegerField(required=False, min_value=256, max_value=32_000)
    flashMaxTokens = serializers.IntegerField(required=False, min_value=64, max_value=2_000)

    def validate_flashModel(self, value: str) -> str:
        return _validate_model_with_role(value, "flash")

    def validate_proModel(self, value: str) -> str:
        return _validate_model_with_role(value, "pro")
