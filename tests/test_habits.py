"""habits app — end-to-end smoke tests.

Happy path (create habit → check-in → today/stats) plus the security-critical
cases (no auth, cross-user isolation) and the frequency/target validation rules.
Auth uses DRF force_authenticate (the real cookie/JWT path is exercised by the
journal/accounts suites)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.core.tz import local_today
from apps.habits.models import HabitLog


@pytest.fixture
def user(db):
    return User.objects.create_user(username="habit_tester", password="pw123456")


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _create(client, **over):
    body = {"name": "Thói quen", "type": "binary", "frequency": "daily"}
    body.update(over)
    r = client.post("/api/v1/habits", body, format="json")
    return r


# ── auth ───────────────────────────────────────────────────────────────────

def test_list_requires_auth():
    assert APIClient().get("/api/v1/habits").status_code == 401


# ── create + validation ──────────────────────────────────────────────────--

def test_create_binary_habit_sets_sort_order(client):
    r = _create(client, name="Thiền")
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["name"] == "Thiền"
    assert body["sort_order"] == 1
    assert body["target_count"] is None


def test_count_habit_requires_target(client):
    r = _create(client, name="Uống nước", type="count")
    assert r.status_code == 400


def test_weekly_days_requires_weekdays(client):
    r = _create(client, name="Gym", frequency="weekly_days", weekdays=[])
    assert r.status_code == 400


def test_weekly_count_requires_target(client):
    r = _create(client, name="Chạy bộ", frequency="weekly_count")
    assert r.status_code == 400


def test_reminder_requires_time(client):
    r = _create(client, name="Vitamin", reminder_enabled=True)
    assert r.status_code == 400


# ── check-ins ───────────────────────────────────────────────────────────────

def test_binary_checkin_and_today(client):
    h = _create(client, name="Đọc sách").json()
    r = client.post("/api/v1/habit-logs", {"habit": h["id"]}, format="json")
    assert r.status_code == 200, r.content
    assert r.json()["completed"] is True

    today = client.get("/api/v1/habits/today").json()
    item = next(i for i in today["items"] if i["habit"]["id"] == h["id"])
    assert item["done"] is True


def test_count_checkin_completes_at_target(client):
    h = _create(client, name="Nước", type="count", target_count=8, unit="ly").json()
    partial = client.post("/api/v1/habit-logs", {"habit": h["id"], "count": 3}, format="json")
    assert partial.json()["completed"] is False
    full = client.post("/api/v1/habit-logs", {"habit": h["id"], "count": 8}, format="json")
    assert full.json()["completed"] is True
    # upsert — still one row for (habit, today)
    assert HabitLog.objects.filter(habit_id=h["id"]).count() == 1


def test_uncheck_deletes_log(client):
    h = _create(client, name="X").json()
    log = client.post("/api/v1/habit-logs", {"habit": h["id"]}, format="json").json()
    d = client.delete(f"/api/v1/habit-logs/{log['id']}")
    assert d.status_code == 204
    assert not HabitLog.objects.filter(id=log["id"]).exists()


# ── stats ────────────────────────────────────────────────────────────────--

def test_stats_streak_and_heatmap(client):
    h = _create(client, name="Daily").json()
    today = local_today()
    for i in range(3):  # today, -1, -2
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        client.post("/api/v1/habit-logs", {"habit": h["id"], "date": d}, format="json")
    s = client.get("/api/v1/habits/stats").json()
    ph = next(p for p in s["habits"] if p["id"] == h["id"])
    assert ph["current_streak"] == 3
    assert ph["longest_streak"] >= 3
    assert s["overall"]["today_completed"] == 1
    assert len(s["heatmap"]) > 0


def test_today_in_progress_does_not_break_streak(client):
    """Logged yesterday + day before, nothing today yet → streak still 2."""
    h = _create(client, name="Daily2").json()
    today = local_today()
    for i in (1, 2):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        client.post("/api/v1/habit-logs", {"habit": h["id"], "date": d}, format="json")
    s = client.get("/api/v1/habits/stats").json()
    ph = next(p for p in s["habits"] if p["id"] == h["id"])
    assert ph["current_streak"] == 2


# ── isolation + archive ──────────────────────────────────────────────────--

def test_cross_user_isolation(client):
    h = _create(client, name="Mine").json()
    other = User.objects.create_user(username="other", password="pw123456")
    oc = APIClient()
    oc.force_authenticate(user=other)
    assert oc.get("/api/v1/habits").json() == []
    # another user cannot check in my habit
    assert oc.post("/api/v1/habit-logs", {"habit": h["id"]}, format="json").status_code == 404


def test_archive_hides_from_default_list(client):
    h = _create(client, name="Old").json()
    assert client.post(f"/api/v1/habits/{h['id']}/archive").status_code == 200
    assert all(x["id"] != h["id"] for x in client.get("/api/v1/habits").json())
    assert any(x["id"] == h["id"] for x in client.get("/api/v1/habits?include_archived=1").json())


def test_reorder(client):
    a = _create(client, name="A").json()
    b = _create(client, name="B").json()
    r = client.post(
        "/api/v1/habits/reorder",
        {"order": [{"id": b["id"], "sort_order": 0}, {"id": a["id"], "sort_order": 1}]},
        format="json",
    )
    assert r.status_code == 200
    listing = client.get("/api/v1/habits").json()
    assert [x["id"] for x in listing] == [b["id"], a["id"]]
