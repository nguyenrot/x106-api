"""Service-token authentication for non-human callers (cron agents, etc.).

A service token is a long-lived random string stored on the caller. On the
server we only ever store its SHA-256 hex digest, in `settings.SERVICE_TOKENS`
as `{service_name: sha256_hex}`. The auth class hashes the inbound header and
compares constant-time against each registered service.

A successful match attaches a `ServiceUser` to `request.user`. `request.auth`
gets set to the matched service name so permissions can scope by name.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from django.conf import settings
from django.utils.crypto import constant_time_compare
from rest_framework.authentication import BaseAuthentication


SERVICE_TOKEN_HEADER = "X-Service-Token"


@dataclass(frozen=True)
class ServiceUser:
    """Stand-in user for a service-token request. Behaves like an
    authenticated, staff-ish principal that is NOT a Django User row."""

    name: str

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False

    @property
    def is_staff(self) -> bool:
        # Service tokens are intentionally NOT staff — let permissions decide
        # by checking the explicit service name instead of relying on is_staff.
        return False

    @property
    def is_active(self) -> bool:
        return True

    @property
    def id(self) -> None:
        return None

    @property
    def pk(self) -> None:
        return None

    def __str__(self) -> str:  # pragma: no cover
        return f"service:{self.name}"


class ServiceTokenAuthentication(BaseAuthentication):
    """Reads `X-Service-Token`, looks it up in `settings.SERVICE_TOKENS`.

    `SERVICE_TOKENS` shape: `{"quotes-agent": "<sha256-hex of raw token>"}`.
    Empty dict → this auth class always returns None (no service tokens
    configured = feature disabled).
    """

    def authenticate(self, request):  # type: ignore[override]
        raw = request.META.get("HTTP_X_SERVICE_TOKEN") or request.headers.get(
            SERVICE_TOKEN_HEADER
        )
        if not raw:
            return None

        tokens: dict[str, str] = getattr(settings, "SERVICE_TOKENS", {}) or {}
        if not tokens:
            return None

        inbound_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        for name, expected_hash in tokens.items():
            if expected_hash and constant_time_compare(inbound_hash, expected_hash):
                return (ServiceUser(name=name), name)

        return None

    def authenticate_header(self, request):  # pragma: no cover
        return SERVICE_TOKEN_HEADER
