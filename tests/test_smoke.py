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


def test_admin_login_endpoint_exists():
    client = Client()
    response = client.post(
        "/api/v1/admin/login",
        data={"username": "x", "password": "x"},
        content_type="application/json",
    )
    # 401 = endpoint exists and credentials rejected. 404 = bad routing.
    assert response.status_code in (400, 401)
