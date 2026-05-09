"""Auth endpoints — login/logout/register (user) + login/logout (admin) + /users/me."""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import AccessToken

from .models import User
from .serializers import (
    AdminLoginSerializer,
    RegisterSerializer,
    UserSerializer,
    UserTokenObtainSerializer,
)


def _set_cookie(response: Response, name: str, value: str, max_age: int) -> None:
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        httponly=True,
        secure=not settings.DEBUG,
        samesite="Lax",
        domain=settings.X106_COOKIE_DOMAIN,
        path="/",
    )


def _clear_cookie(response: Response, name: str) -> None:
    response.delete_cookie(name, domain=settings.X106_COOKIE_DOMAIN, path="/")


def _user_token(user: User) -> str:
    token = UserTokenObtainSerializer.get_token(user)
    return str(token.access_token)


class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        token = _user_token(user)
        response = Response(
            {"user": UserSerializer(user).data, "token": token},
            status=status.HTTP_201_CREATED,
        )
        _set_cookie(response, settings.X106_SESSION_COOKIE, token, settings.X106_SESSION_COOKIE_MAX_AGE)
        return response


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        username = (request.data.get("username") or "").strip()
        password = request.data.get("password") or ""
        if not username or not password:
            return Response(
                {"error": "username and password required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user = authenticate(request, username=username, password=password)
        if user is None:
            return Response(
                {"error": "invalid username or password"},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        token = _user_token(user)
        response = Response({"user": UserSerializer(user).data, "token": token})
        _set_cookie(response, settings.X106_SESSION_COOKIE, token, settings.X106_SESSION_COOKIE_MAX_AGE)
        return response


class LogoutView(APIView):
    permission_classes = [AllowAny]

    def post(self, _request):
        response = Response({"message": "logged out"})
        _clear_cookie(response, settings.X106_SESSION_COOKIE)
        return response


class AdminLoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = AdminLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(
            request,
            username=serializer.validated_data["username"],
            password=serializer.validated_data["password"],
        )
        if user is None or not user.is_staff:
            return Response(
                {"error": "invalid credentials"},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        token = AccessToken.for_user(user)
        token.set_exp(lifetime=timedelta(seconds=settings.X106_ADMIN_COOKIE_MAX_AGE))
        token["role"] = "admin"
        token["username"] = user.username
        token_str = str(token)
        response = Response({"token": token_str})
        _set_cookie(response, settings.X106_ADMIN_COOKIE, token_str, settings.X106_ADMIN_COOKIE_MAX_AGE)
        return response


class AdminLogoutView(APIView):
    permission_classes = [AllowAny]

    def post(self, _request):
        response = Response({"message": "logged out"})
        _clear_cookie(response, settings.X106_ADMIN_COOKIE)
        return response


class UsersMeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)
