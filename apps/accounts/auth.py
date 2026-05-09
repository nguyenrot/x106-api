"""Custom DRF authentication that reads the X106 cookies and falls back to Bearer."""

from __future__ import annotations

from django.conf import settings
from rest_framework_simplejwt.authentication import JWTAuthentication


class JWTCookieAuthentication(JWTAuthentication):
    """Token discovery order: x106_session cookie → x106_admin cookie → Authorization header.

    Mirrors internal/middleware/auth.go + admin.go from the Go service. Both
    cookies are signed with the same JWT_SECRET; the admin path is just a
    different claim (`role: admin`) on the token, enforced by `IsAdminToken`.
    """

    def authenticate(self, request):  # type: ignore[override]
        raw = (
            request.COOKIES.get(settings.X106_SESSION_COOKIE)
            or request.COOKIES.get(settings.X106_ADMIN_COOKIE)
        )
        if raw:
            validated = self.get_validated_token(raw)
            return (self.get_user(validated), validated)
        return super().authenticate(request)
