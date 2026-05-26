from __future__ import annotations

from rest_framework import serializers

from .models import Artwork


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


class PublicArtworkSerializer(serializers.ModelSerializer):
    owner_username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = Artwork
        fields = [
            "id",
            "title",
            "scene",
            "thumbnail_data_url",
            "owner_username",
            "created_at",
        ]
