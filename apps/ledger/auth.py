"""Bearer-token authentication for ledger accounts.

The token is opaque, returned exactly once at account creation, and stored on
the server only as a SHA-256 hash. There is no expiry — losing the token =
losing the account.
"""

from __future__ import annotations

import hashlib

from rest_framework import authentication, exceptions

from .models import LedgerAccount


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class LedgerTokenAuthentication(authentication.BaseAuthentication):
    keyword = b"bearer"

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).split()
        if not header or header[0].lower() != self.keyword:
            return None
        if len(header) != 2:
            raise exceptions.AuthenticationFailed("Invalid Authorization header.")
        try:
            raw = header[1].decode("utf-8")
        except UnicodeError as exc:
            raise exceptions.AuthenticationFailed("Invalid token encoding.") from exc
        if not raw:
            raise exceptions.AuthenticationFailed("Empty token.")

        try:
            account = LedgerAccount.objects.get(token_hash=hash_token(raw))
        except LedgerAccount.DoesNotExist as exc:
            raise exceptions.AuthenticationFailed("Invalid token.") from exc
        return (account, None)

    def authenticate_header(self, request):
        return "Bearer"
