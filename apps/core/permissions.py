"""Custom DRF permissions."""

from rest_framework.permissions import BasePermission


class IsAdminToken(BasePermission):
    """Authenticated request whose JWT carries `role: admin`.

    Set on tokens minted by the admin login flow; the regular user-login flow
    does not set this claim, so only true admins pass."""

    message = "Admin role required."

    def has_permission(self, request, view) -> bool:  # type: ignore[override]
        if not request.user or not request.user.is_authenticated:
            return False
        token = getattr(request, "auth", None)
        if token is not None and token.get("role") == "admin":
            return True
        # Fallback: a Django superuser/staff is always allowed (e.g. Django admin
        # session cookie) so /admin/ UI users can also call admin APIs.
        return bool(getattr(request.user, "is_staff", False))
