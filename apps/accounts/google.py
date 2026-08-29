"""Sign in with Google — auth-code exchange, claim checks, and account resolution.

The browser runs Google's popup code flow (`initCodeClient`) and hands us a one-shot
authorization code. We exchange it server-side for an `id_token` and read the identity
out of that. Ported from the same flow in the lumi backend, adapted to this schema:
`display_name` / `avatar_url` live on the user row here, not on a profile relation.

WHY WE DON'T VERIFY THE ID TOKEN'S SIGNATURE
--------------------------------------------
The token is not something a caller handed us — we fetched it ourselves over TLS from
`https://oauth2.googleapis.com/token`, using a client secret only this server knows.
OIDC Core §3.1.3.7 says exactly this case may substitute TLS server authentication for
signature validation. A forged token would require breaking TLS to Google.

We still check `iss` / `aud` / `exp` / `email_verified`, because those catch the failure
that actually happens: a misconfigured or swapped OAuth client.

If this ever moves to the implicit/One-Tap flow — where the *browser* hands us a
credential — the exemption evaporates and full signature verification becomes mandatory.
"""

from __future__ import annotations

import logging
import re
import time

import httpx
import jwt
from django.conf import settings
from django.db import IntegrityError

from .models import User

log = logging.getLogger("apps.accounts")

_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
# `users.username` is what every other X106 app displays and logs in with; keep the
# generated ones inside the charset the register endpoint accepts.
_USERNAME_STRIP = re.compile(r"[^a-z0-9_.-]")
_USERNAME_MAX = 50
# Tolerance for clock drift between this VPS and Google when checking `exp`.
_LEEWAY_SEC = 60


class GoogleNotConfigured(Exception):
    """No OAuth client on this server — the endpoint answers 503."""


class GoogleAuthError(Exception):
    """Anything that should surface to the visitor as "try again" (HTTP 400)."""

    def __init__(self, message: str = "Google sign-in failed. Please try again."):
        super().__init__(message)
        self.message = message


def is_configured() -> bool:
    return bool(settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET)


def exchange_code(code: str) -> dict:
    """Trade a one-shot authorization code for Google's token response.

    `redirect_uri="postmessage"` is the magic value the JS popup flow expects; there is
    no registered redirect URI for this client and there must not be one.
    """
    if not is_configured():
        raise GoogleNotConfigured()
    try:
        response = httpx.post(
            settings.GOOGLE_TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                "redirect_uri": "postmessage",
                "grant_type": "authorization_code",
            },
            timeout=10.0,
        )
    except httpx.HTTPError:
        log.exception("google token exchange: transport error")
        raise GoogleAuthError("Không gọi được Google. Hãy thử lại.") from None

    if response.status_code != 200:
        # Log Google's own reason (invalid_grant, redirect_uri_mismatch, …) — it is the
        # difference between "user double-clicked" and "the OAuth client is misconfigured".
        log.warning("google token exchange failed: %s %s", response.status_code, response.text[:400])
        raise GoogleAuthError()
    return response.json()


def claims_from_id_token(raw: str) -> dict:
    """Decode + sanity-check the id_token. See the module docstring on signatures."""
    try:
        claims = jwt.decode(raw, options={"verify_signature": False})
    except jwt.PyJWTError:
        log.warning("google id_token could not be decoded")
        raise GoogleAuthError() from None

    if claims.get("iss") not in _ISSUERS:
        log.warning("google id_token: unexpected issuer %r", claims.get("iss"))
        raise GoogleAuthError()

    aud = claims.get("aud")
    audiences = aud if isinstance(aud, list) else [aud]
    if settings.GOOGLE_OAUTH_CLIENT_ID not in audiences:
        # Someone pointed a different OAuth client at us, or the env vars drifted apart.
        log.warning("google id_token: audience mismatch %r", aud)
        raise GoogleAuthError()

    exp = claims.get("exp")
    if not isinstance(exp, int | float) or exp + _LEEWAY_SEC < time.time():
        raise GoogleAuthError("Lần đăng nhập Google đó đã hết hạn. Hãy thử lại.")

    if not claims.get("sub"):
        raise GoogleAuthError()

    email = (claims.get("email") or "").strip().lower()
    verified = claims.get("email_verified")
    if not email or verified not in (True, "true"):
        # We match accounts by address further down, so an unverified address would be a
        # way to claim someone else's account.
        raise GoogleAuthError("Tài khoản Google này chưa có email được xác thực.")

    claims["email"] = email
    return claims


def _unique_username(preferred: str) -> str:
    """First free username at or after `preferred`.

    Check-then-create, so it races; `resolve_user` catches the IntegrityError and retries.
    """
    candidate = _USERNAME_STRIP.sub("", (preferred or "").lower())[:_USERNAME_MAX].strip(".-")
    candidate = candidate or "daohuu"
    if not User.objects.filter(username__iexact=candidate).exists():
        return candidate
    n = 1
    while True:
        n += 1
        alt = f"{candidate[: _USERNAME_MAX - 5]}-{n}"
        if not User.objects.filter(username__iexact=alt).exists():
            return alt


def _apply_profile(user: User, claims: dict) -> None:
    """Fill in blanks from Google — never overwrite something the user already set."""
    dirty: list[str] = []
    name = (claims.get("name") or "").strip()
    if name and not user.display_name:
        user.display_name = name[:100]
        dirty.append("display_name")
    picture = (claims.get("picture") or "").strip()
    if picture and not user.avatar_url:
        user.avatar_url = picture[:500]
        dirty.append("avatar_url")
    if dirty:
        user.save(update_fields=[*dirty, "updated_at"])


def resolve_user(claims: dict) -> tuple[User, bool]:
    """Map verified Google claims to a User. Returns (user, created).

    Resolution order is deliberate:

    1. `google_sub` — the identity key once linked. Survives the user changing their
       Google address.
    2. verified email — adopts a pre-existing X106 account so signing in with Google
       doesn't strand the username/password account someone already plays under. Safe
       because Google asserts the address is verified.
    3. otherwise, a brand-new account with no usable password.

    `users.email` has no unique constraint here (it predates Django and is NULL for most
    rows), so an ambiguous address is refused rather than resolved to an arbitrary row.
    """
    sub = claims["sub"]
    user = User.objects.filter(google_sub=sub).first()
    if user is not None:
        _apply_profile(user, claims)
        return user, False

    email = claims["email"]
    matches = list(User.objects.filter(email__iexact=email)[:2])
    if len(matches) > 1:
        log.error("google sign-in: %s matches more than one account", email)
        raise GoogleAuthError("Email này đang gắn với nhiều tài khoản X106. Hãy liên hệ quản trị.")
    if matches:
        existing = matches[0]
        existing.google_sub = sub
        existing.save(update_fields=["google_sub", "updated_at"])
        _apply_profile(existing, claims)
        log.info("google sign-in linked to existing account id=%s", existing.pk)
        return existing, False

    username = _unique_username(email.split("@", 1)[0])
    try:
        # create_user with password=None sets an unusable hash — identity lives with Google.
        user = User.objects.create_user(
            username=username,
            password=None,
            email=email,
            google_sub=sub,
        )
    except IntegrityError:
        # Raced with a concurrent first sign-in for the same account, or the username was
        # taken between the check and the insert.
        raced = User.objects.filter(google_sub=sub).first()
        if raced is None:
            log.exception("google sign-in: could not create account for sub=%s", sub)
            raise GoogleAuthError() from None
        return raced, False

    _apply_profile(user, claims)
    return user, True


def sign_in(code: str) -> tuple[User, bool]:
    """The whole flow: code → tokens → claims → user."""
    tokens = exchange_code(code)
    raw_id_token = tokens.get("id_token")
    if not raw_id_token:
        log.warning("google token response had no id_token (scopes missing openid?)")
        raise GoogleAuthError()
    return resolve_user(claims_from_id_token(raw_id_token))
