"""Auth endpoints — login/logout/register (user) + login/logout (admin) + /users/me + admin user mgmt."""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate
from django.db.models import Q
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import AccessToken

from apps.core.permissions import IsAdminToken

from . import google
from .models import User
from .serializers import (
    AdminLoginSerializer,
    GoogleAuthSerializer,
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


class GoogleAuthView(APIView):
    """POST /api/v1/auth/google — sign in (or sign up) with a Google auth code.

    Same `{user, token}` envelope as login/register, so callers have one
    session-establishing shape to handle. Throttled because every call makes an
    outbound request to Google's token endpoint.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_google"

    def post(self, request):
        serializer = GoogleAuthSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user, created = google.sign_in(serializer.validated_data["code"])
        except google.GoogleNotConfigured:
            return Response(
                {"error": "Google sign-in is not configured on this server."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except google.GoogleAuthError as failure:
            return Response({"error": failure.message}, status=status.HTTP_400_BAD_REQUEST)

        if not user.is_active:
            return Response(
                {"error": "Tài khoản này đã bị vô hiệu hoá."},
                status=status.HTTP_403_FORBIDDEN,
            )
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])

        token = _user_token(user)
        response = Response(
            {"user": UserSerializer(user).data, "token": token, "created": created},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
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


class AdminVerifyView(APIView):
    permission_classes = [IsAdminToken]

    def get(self, _request):
        return Response({"ok": True})


class UsersMeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)


# ─── Admin: user management ────────────────────────────────────────────


def _user_row(u: User) -> dict:
    # Legacy rows (created before Django model enforcement) can have NULL
    # username/email — coerce to "" so the frontend never sees a null string.
    return {
        "id":          u.id,
        "username":    u.username or "",
        "displayName": u.display_name or "",
        "email":       u.email or "",
        "isActive":    bool(u.is_active),
        "isStaff":     bool(u.is_staff),
        "isSuperuser": bool(u.is_superuser),
        "lastLogin":   u.last_login.isoformat() if u.last_login else "",
        "createdAt":   u.created_at.isoformat() if u.created_at else "",
    }


class AdminUsersViewSet(viewsets.ViewSet):
    """List / activate / deactivate / delete users.

    Safeguards:
      - You cannot modify your own account from this UI (use the user-facing
        flow or Django admin instead).
      - Superusers cannot be deleted or deactivated from this UI — manage them
        in `/admin/` to avoid accidentally locking yourself out.
    """

    permission_classes = [IsAdminToken]
    lookup_field = "id"
    lookup_value_regex = r"[^/]+"

    def list(self, request):
        params = request.query_params

        try:
            limit = max(1, min(int(params.get("limit", 50)), 200))
        except (TypeError, ValueError):
            limit = 50
        try:
            offset = max(0, int(params.get("offset", 0)))
        except (TypeError, ValueError):
            offset = 0

        q = (params.get("q") or "").strip()
        active_filter = (params.get("active") or "").strip().lower()

        qs = User.objects.all()
        if q:
            qs = qs.filter(Q(username__icontains=q) | Q(display_name__icontains=q))
        if active_filter == "active":
            qs = qs.filter(is_active=True)
        elif active_filter == "inactive":
            qs = qs.filter(is_active=False)

        total = qs.count()
        rows = list(qs.order_by("-created_at")[offset : offset + limit])

        return Response(
            {
                "items":  [_user_row(u) for u in rows],
                "total":  total,
                "limit":  limit,
                "offset": offset,
            }
        )

    def destroy(self, request, id: str | None = None):
        if request.user and getattr(request.user, "id", None) == id:
            return Response(
                {"error": "Cannot delete your own account."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            user = User.objects.get(id=id)
        except User.DoesNotExist:
            return Response(
                {"error": "user not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        if user.is_superuser:
            return Response(
                {"error": "Cannot delete a superuser. Use Django admin."},
                status=status.HTTP_403_FORBIDDEN,
            )
        # Django ORM cascade for any related model that declares ForeignKey(User)
        # with on_delete=CASCADE. Non-ORM rows referencing user_id (e.g. llm_jobs
        # if it lacks a Django FK) will become orphaned and are tolerable for
        # this small personal admin tool.
        user.delete()
        return Response({"message": "deleted", "id": id})

    @action(detail=True, methods=["post"], url_path="activate")
    def activate(self, request, id: str | None = None):
        return self._set_active(request, id, True)

    @action(detail=True, methods=["post"], url_path="deactivate")
    def deactivate(self, request, id: str | None = None):
        return self._set_active(request, id, False)

    def _set_active(self, request, user_id: str | None, value: bool):
        if request.user and getattr(request.user, "id", None) == user_id:
            return Response(
                {"error": "Cannot change your own active status."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response(
                {"error": "user not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        if user.is_superuser:
            return Response(
                {"error": "Cannot change a superuser's active status from here."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if user.is_active == value:
            return Response(_user_row(user))
        user.is_active = value
        user.save(update_fields=["is_active", "updated_at"])
        return Response(_user_row(user))
