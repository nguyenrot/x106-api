"""Sign in with Google — claim checks and account resolution.

`exchange_code` is the one seam that touches the network, so it is the only thing patched.
Everything below it (claim checks, username generation, account linking) runs for real.
"""

from __future__ import annotations

import time

import jwt
import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client

from apps.accounts import google

CLIENT_ID = "test-client-id.apps.googleusercontent.com"
User = get_user_model()


@pytest.fixture(autouse=True)
def _configured(settings):
    settings.GOOGLE_OAUTH_CLIENT_ID = CLIENT_ID
    settings.GOOGLE_OAUTH_CLIENT_SECRET = "test-secret"
    # The endpoint is throttled per IP; a shared locmem cache would leak counts between
    # tests and fail whichever one happened to run 31st.
    cache.clear()


def id_token(**overrides) -> str:
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "exp": int(time.time()) + 600,
        "sub": "google-sub-1",
        "email": "DaoHuu@example.com",
        "email_verified": True,
        "name": "Đạo Hữu",
        "picture": "https://lh3.googleusercontent.com/a/portrait",
    }
    claims.update(overrides)
    # Signature is irrelevant: the flow fetches this token itself over TLS and reads it
    # without verifying (see google.py's module docstring).
    return jwt.encode(claims, "irrelevant", algorithm="HS256")


def sign_in(monkeypatch, **overrides):
    monkeypatch.setattr(google, "exchange_code", lambda _code: {"id_token": id_token(**overrides)})
    return Client().post(
        "/api/v1/auth/google",
        data={"code": "one-shot-code"},
        content_type="application/json",
    )


def test_missing_code_is_rejected():
    response = Client().post("/api/v1/auth/google", data={}, content_type="application/json")
    assert response.status_code == 400


def test_unconfigured_server_answers_503(settings, monkeypatch):
    settings.GOOGLE_OAUTH_CLIENT_ID = ""
    settings.GOOGLE_OAUTH_CLIENT_SECRET = ""
    response = Client().post(
        "/api/v1/auth/google",
        data={"code": "one-shot-code"},
        content_type="application/json",
    )
    assert response.status_code == 503


def test_first_sign_in_creates_account(monkeypatch):
    response = sign_in(monkeypatch)
    assert response.status_code == 201
    body = response.json()
    assert body["created"] is True
    assert body["token"]

    user = User.objects.get(google_sub="google-sub-1")
    assert user.email == "daohuu@example.com"  # normalized
    assert user.username == "daohuu"
    assert user.display_name == "Đạo Hữu"
    assert not user.has_usable_password()  # identity lives with Google


def test_second_sign_in_reuses_the_same_account(monkeypatch):
    first = sign_in(monkeypatch)
    assert first.status_code == 201
    second = sign_in(monkeypatch)
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert User.objects.filter(google_sub="google-sub-1").count() == 1


def test_verified_email_adopts_an_existing_password_account(monkeypatch):
    """Otherwise everyone who already plays under a username would be stranded."""
    existing = User.objects.create_user(username="tan_mac", password="mat-khau", email="daohuu@example.com")

    response = sign_in(monkeypatch)

    assert response.status_code == 200
    existing.refresh_from_db()
    assert existing.google_sub == "google-sub-1"
    assert existing.username == "tan_mac"  # their own name survives
    assert User.objects.count() == 1


def test_username_collision_gets_a_suffix(monkeypatch):
    User.objects.create_user(username="daohuu", password="x")
    response = sign_in(monkeypatch)
    assert response.status_code == 201
    assert User.objects.get(google_sub="google-sub-1").username == "daohuu-2"


def test_ambiguous_email_is_refused_rather_than_guessed(monkeypatch):
    """`users.email` has no unique constraint, so two rows can share an address."""
    User.objects.create_user(username="one", password="x", email="daohuu@example.com")
    User.objects.create_user(username="two", password="x", email="DAOHUU@example.com")

    response = sign_in(monkeypatch)

    assert response.status_code == 400
    assert not User.objects.filter(google_sub="google-sub-1").exists()


def test_unverified_email_is_rejected(monkeypatch):
    response = sign_in(monkeypatch, email_verified=False)
    assert response.status_code == 400
    assert User.objects.count() == 0


def test_audience_mismatch_is_rejected(monkeypatch):
    """Catches the failure that actually happens: a swapped OAuth client."""
    response = sign_in(monkeypatch, aud="someone-elses-client.apps.googleusercontent.com")
    assert response.status_code == 400
    assert User.objects.count() == 0


def test_wrong_issuer_is_rejected(monkeypatch):
    response = sign_in(monkeypatch, iss="https://evil.example.com")
    assert response.status_code == 400


def test_expired_token_is_rejected(monkeypatch):
    response = sign_in(monkeypatch, exp=int(time.time()) - 3600)
    assert response.status_code == 400


def test_inactive_account_cannot_sign_in(monkeypatch):
    User.objects.create_user(
        username="banned", password="x", email="daohuu@example.com", is_active=False
    )
    response = sign_in(monkeypatch)
    assert response.status_code == 403
