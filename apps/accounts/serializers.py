from __future__ import annotations

from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import User


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "display_name",
            "avatar_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class RegisterSerializer(serializers.Serializer):
    username = serializers.CharField(min_length=3, max_length=50)
    password = serializers.CharField(min_length=6, max_length=255, write_only=True)

    def validate_username(self, value: str) -> str:
        value = value.strip()
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("username already taken")
        return value

    def create(self, validated_data: dict) -> User:
        return User.objects.create_user(
            username=validated_data["username"],
            password=validated_data["password"],
        )


class GoogleAuthSerializer(serializers.Serializer):
    """The one-shot authorization code from Google's popup code flow."""

    code = serializers.CharField(trim_whitespace=True, max_length=2048, write_only=True)


class UserTokenObtainSerializer(TokenObtainPairSerializer):
    """Adds `username` to JWT claims on top of simplejwt's defaults."""

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["username"] = user.username
        return token


class AdminLoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)
