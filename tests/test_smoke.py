"""Smoke tests — guarantee Django config loads + URL routing works in CI."""

from django.test import Client
from django.urls import reverse


def test_health_endpoint():
    client = Client()
    response = client.get(reverse("health"))
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_unauthenticated_users_me_returns_401():
    client = Client()
    response = client.get("/api/v1/users/me")
    assert response.status_code == 401


def test_public_content_endpoint_routing():
    """The route should resolve even if there's no row in site_content."""
    client = Client()
    response = client.get("/api/v1/content/nope/nothing")
    # 404 = route resolved, no matching row. Anything else = routing broken.
    assert response.status_code == 404


def test_now_weather_endpoint_routing(monkeypatch):
    """The endpoint should always return 200 — `get_weather()` swallows
    network errors and falls back to a placeholder payload. Tests must not
    hit the real Open-Meteo API."""
    from apps.core.services import weather as weather_mod
    monkeypatch.setattr(
        weather_mod,
        "_fetch_remote",
        lambda: {
            "temp_c": 27,
            "code": 1,
            "description": {"en": "mostly clear", "vi": "trời gần quang"},
            "place": "Mỹ Khê",
        },
    )
    # reset cache so the mocked _fetch_remote actually runs
    weather_mod._cache.clear()
    weather_mod._cache.update({"ts": 0.0, "data": None})

    client = Client()
    response = client.get("/api/v1/now/weather")
    assert response.status_code == 200
    body = response.json()
    assert body["temp_c"] == 27
    assert body["description"] == {"en": "mostly clear", "vi": "trời gần quang"}
    assert body["place"] == "Mỹ Khê"


def test_admin_content_section_delete_requires_admin():
    """DELETE without admin token → 401, not 405. Confirms the route is wired."""
    client = Client()
    response = client.delete("/api/v1/admin/content/vibe-hub/__nonexistent__")
    assert response.status_code in (401, 403)


def test_retired_app_routes_are_gone():
    """journal / habits / ledger / studio were unmounted 2026-08-29. 404 = gone."""
    client = Client()
    for path in (
        "/api/v1/journal/vibes",
        "/api/v1/habits",
        "/api/v1/habit-logs",
        "/api/v1/ledger/accounts",
        "/api/v1/artworks",
        "/api/v1/public/artworks/this-token-does-not-exist",
    ):
        response = client.get(path)
        assert response.status_code == 404, path


def test_admin_login_endpoint_exists():
    client = Client()
    response = client.post(
        "/api/v1/admin/login",
        data={"username": "x", "password": "x"},
        content_type="application/json",
    )
    # 401 = endpoint exists and credentials rejected. 404 = bad routing.
    assert response.status_code in (400, 401)
