"""Custom DRF permissions."""

from rest_framework.permissions import BasePermission

from .auth import ServiceUser


class IsAdminToken(BasePermission):
    """Authenticated request whose JWT carries `role: admin`.

    Set on tokens minted by the admin login flow; the regular user-login flow
    does not set this claim, so only true admins pass."""

    message = "Admin role required."

    def has_permission(self, request, view) -> bool:  # type: ignore[override]
        if not request.user or not request.user.is_authenticated:
            return False
        # Service-token principals are never "admin"; they pass via IsServiceToken.
        if isinstance(request.user, ServiceUser):
            return False
        token = getattr(request, "auth", None)
        if token is not None and hasattr(token, "get") and token.get("role") == "admin":
            return True
        # Fallback: a Django superuser/staff is always allowed (e.g. Django admin
        # session cookie) so /admin/ UI users can also call admin APIs.
        return bool(getattr(request.user, "is_staff", False))


class IsServiceToken(BasePermission):
    """Service-token request whose service name is in the view's allowlist.

    Views opt in by setting `allowed_services = ["quotes-agent", ...]`. A view
    without that attribute denies every service token — strict default.
    """

    message = "Service token not authorized for this endpoint."

    def has_permission(self, request, view) -> bool:  # type: ignore[override]
        user = getattr(request, "user", None)
        if not isinstance(user, ServiceUser):
            return False
        allowed = set(getattr(view, "allowed_services", ()) or ())
        return user.name in allowed


class IsAdminOrAllowedService(BasePermission):
    """Pass if either admin OR service-token (with name in `view.allowed_services`).

    Convenience for endpoints (like AdminQuoteViewSet) that should be callable
    by both human admins and the daily quotes agent without duplicating logic.
    """

    message = "Admin role or authorized service token required."

    def has_permission(self, request, view) -> bool:  # type: ignore[override]
        if IsAdminToken().has_permission(request, view):
            return True
        return IsServiceToken().has_permission(request, view)
