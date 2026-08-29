"""Vấn Đạo cloud save — ownership and the revision guard against lost progress."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from apps.accounts.serializers import UserTokenObtainSerializer
from apps.vandao.models import GameSave

User = get_user_model()
URL = "/api/v1/vandao/save"


def bearer(user) -> dict:
    token = UserTokenObtainSerializer.get_token(user).access_token
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


@pytest.fixture
def player():
    return User.objects.create_user(username="tan_mac", password="mat-khau")


def put(client, user, data, base_revision, force=False):
    return client.put(
        URL,
        data={"data": data, "baseRevision": base_revision, "force": force},
        content_type="application/json",
        **bearer(user),
    )


def test_requires_authentication():
    client = Client()
    assert client.get(URL).status_code == 401
    assert client.put(URL, data={}, content_type="application/json").status_code == 401


def test_empty_account_has_no_save(player):
    response = Client().get(URL, **bearer(player))
    assert response.status_code == 200
    assert response.json() == {"save": None}


def test_first_write_starts_at_revision_one(player):
    client = Client()
    response = put(client, player, {"realmIndex": 2}, 0)

    assert response.status_code == 200
    assert response.json()["revision"] == 1

    stored = client.get(URL, **bearer(player)).json()["save"]
    assert stored["data"] == {"realmIndex": 2}
    assert stored["revision"] == 1


def test_each_accepted_write_bumps_the_revision(player):
    client = Client()
    put(client, player, {"realmIndex": 1}, 0)
    response = put(client, player, {"realmIndex": 2}, 1)
    assert response.json()["revision"] == 2


def test_stale_revision_conflicts_and_returns_the_server_copy(player):
    """The client must be able to show both copies, so 409 carries the stored save."""
    client = Client()
    put(client, player, {"realmIndex": 5}, 0)

    # A second device that still thinks the cloud is empty.
    response = put(client, player, {"realmIndex": 1}, 0)

    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "conflict"
    assert body["save"]["data"] == {"realmIndex": 5}
    # The losing write must not have landed.
    assert GameSave.objects.get(user=player).data == {"realmIndex": 5}


def test_force_overwrites_a_conflicting_save(player):
    """What the player chose in the conflict prompt."""
    client = Client()
    put(client, player, {"realmIndex": 5}, 0)

    response = put(client, player, {"realmIndex": 1}, 0, force=True)

    assert response.status_code == 200
    assert response.json()["revision"] == 2
    assert GameSave.objects.get(user=player).data == {"realmIndex": 1}


def test_saves_are_per_account(player):
    other = User.objects.create_user(username="other_dao", password="x")
    client = Client()
    put(client, player, {"realmIndex": 9}, 0)

    assert client.get(URL, **bearer(other)).json() == {"save": None}


def test_oversized_save_is_rejected(player):
    response = put(Client(), player, {"junk": "x" * 70_000}, 0)
    assert response.status_code == 400
    assert not GameSave.objects.filter(user=player).exists()


def test_non_object_save_is_rejected(player):
    response = put(Client(), player, ["not", "an", "object"], 0)
    assert response.status_code == 400
