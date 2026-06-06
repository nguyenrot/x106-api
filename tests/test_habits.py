"""habits app — end-to-end smoke tests (token auth, shared with ledger).

Auth is the ledger token model: POST /habits/accounts with a 10-char token
creates a (shared) LedgerAccount; every other call sends Authorization: Bearer.
Includes a test that the same token works on /ledger/* too.
"""

from __future__ import annotations

import secrets
import string
from datetime import timedelta

import pytest
from django.test import Client

from apps.core.tz import local_today
from apps.habits.models import HabitLog


def _token() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(10))


def _create_account(client: Client) -> str:
    raw = _token()
    r = client.post("/api/v1/habits/accounts", data={"token": raw}, content_type="application/json")
    assert r.status_code == 201, r.content
    return raw


def _post(client: Client, path: str, body: dict, token: str):
    return client.post(
        path, data=body, content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}"
    )


def _get(client: Client, path: str, token: str):
    return client.get(path, HTTP_AUTHORIZATION=f"Bearer {token}")


def _delete(client: Client, path: str, token: str):
    return client.delete(path, HTTP_AUTHORIZATION=f"Bearer {token}")


def _new_habit(client: Client, token: str, **over):
    body = {"name": "Thói quen", "type": "binary", "frequency": "daily"}
    body.update(over)
    return _post(client, "/api/v1/habits", body, token)


@pytest.fixture
def token(db):
    return _create_account(Client())


# ── accounts / auth ─────────────────────────────────────────────────────--

def test_list_requires_auth(db):
    assert Client().get("/api/v1/habits").status_code == 401


def test_create_account_rejects_bad_token(db):
    c = Client()
    for bad in ["short", "toolongtoken", "with space", "ăăăăăăăăăă", ""]:
        r = c.post("/api/v1/habits/accounts", data={"token": bad}, content_type="application/json")
        assert r.status_code == 400, (bad, r.content)


def test_create_account_rejects_duplicate(db):
    c = Client()
    raw = _token()
    assert c.post(
        "/api/v1/habits/accounts", data={"token": raw}, content_type="application/json"
    ).status_code == 201
    assert c.post(
        "/api/v1/habits/accounts", data={"token": raw}, content_type="application/json"
    ).status_code == 409


# ── create + validation ──────────────────────────────────────────────────

def test_create_binary_sets_sort_order(token):
    r = _new_habit(Client(), token, name="Thiền")
    assert r.status_code == 201, r.content
    assert r.json()["sort_order"] == 1
    assert r.json()["target_count"] is None


def test_count_requires_target(token):
    assert _new_habit(Client(), token, name="Nước", type="count").status_code == 400


def test_weekly_days_requires_weekdays(token):
    assert _new_habit(Client(), token, name="Gym", frequency="weekly_days", weekdays=[]).status_code == 400


# ── check-ins ──────────────────────────────────────────────────────────--

def test_binary_checkin_and_today(token):
    c = Client()
    h = _new_habit(c, token, name="Đọc sách").json()
    r = _post(c, "/api/v1/habit-logs", {"habit": h["id"]}, token)
    assert r.status_code == 200, r.content
    assert r.json()["completed"] is True
    today = _get(c, "/api/v1/habits/today", token).json()
    item = next(i for i in today["items"] if i["habit"]["id"] == h["id"])
    assert item["done"] is True


def test_count_checkin_completes_at_target(token):
    c = Client()
    h = _new_habit(c, token, name="Nước", type="count", target_count=8, unit="ly").json()
    assert _post(c, "/api/v1/habit-logs", {"habit": h["id"], "count": 3}, token).json()["completed"] is False
    assert _post(c, "/api/v1/habit-logs", {"habit": h["id"], "count": 8}, token).json()["completed"] is True
    assert HabitLog.objects.filter(habit_id=h["id"]).count() == 1


def test_uncheck_deletes_log(token):
    c = Client()
    h = _new_habit(c, token, name="X").json()
    log = _post(c, "/api/v1/habit-logs", {"habit": h["id"]}, token).json()
    assert _delete(c, f"/api/v1/habit-logs/{log['id']}", token).status_code == 204
    assert not HabitLog.objects.filter(id=log["id"]).exists()


# ── stats ──────────────────────────────────────────────────────────────--

def test_stats_streak_and_heatmap(token):
    c = Client()
    h = _new_habit(c, token, name="Daily").json()
    today = local_today()
    for i in range(3):
        _post(c, "/api/v1/habit-logs", {"habit": h["id"], "date": (today - timedelta(days=i)).strftime("%Y-%m-%d")}, token)
    s = _get(c, "/api/v1/habits/stats", token).json()
    ph = next(p for p in s["habits"] if p["id"] == h["id"])
    assert ph["current_streak"] == 3
    assert ph["longest_streak"] >= 3
    assert s["overall"]["today_completed"] == 1
    assert len(s["heatmap"]) > 0


def test_today_in_progress_does_not_break_streak(token):
    c = Client()
    h = _new_habit(c, token, name="Daily2").json()
    today = local_today()
    for i in (1, 2):
        _post(c, "/api/v1/habit-logs", {"habit": h["id"], "date": (today - timedelta(days=i)).strftime("%Y-%m-%d")}, token)
    s = _get(c, "/api/v1/habits/stats", token).json()
    ph = next(p for p in s["habits"] if p["id"] == h["id"])
    assert ph["current_streak"] == 2


# ── isolation + archive + reorder ──────────────────────────────────────--

def test_cross_account_isolation(token):
    c = Client()
    h = _new_habit(c, token, name="Mine").json()
    other = _create_account(Client())
    assert _get(c, "/api/v1/habits", other).json() == []
    assert _post(c, "/api/v1/habit-logs", {"habit": h["id"]}, other).status_code == 404


def test_archive_hides_from_default_list(token):
    c = Client()
    h = _new_habit(c, token, name="Old").json()
    assert _post(c, f"/api/v1/habits/{h['id']}/archive", {}, token).status_code == 200
    assert all(x["id"] != h["id"] for x in _get(c, "/api/v1/habits", token).json())
    assert any(x["id"] == h["id"] for x in _get(c, "/api/v1/habits?include_archived=1", token).json())


def test_reorder(token):
    c = Client()
    a = _new_habit(c, token, name="A").json()
    b = _new_habit(c, token, name="B").json()
    r = _post(
        c, "/api/v1/habits/reorder",
        {"order": [{"id": b["id"], "sort_order": 0}, {"id": a["id"], "sort_order": 1}]}, token,
    )
    assert r.status_code == 200
    assert [x["id"] for x in _get(c, "/api/v1/habits", token).json()] == [b["id"], a["id"]]


# ── shared token across services ───────────────────────────────────────--

def test_token_shared_with_ledger(token):
    """A token created on habits authenticates on ledger, and vice versa."""
    c = Client()
    # habits-created token works on ledger
    assert _get(c, "/api/v1/ledger/me", token).status_code == 200
    # ledger-created token works on habits
    raw = _token()
    assert c.post(
        "/api/v1/ledger/accounts", data={"token": raw}, content_type="application/json"
    ).status_code == 201
    assert _new_habit(c, raw, name="Từ ledger").status_code == 201
    assert _get(c, "/api/v1/habits/me", raw).status_code == 200
