"""Smoke tests for the studio app — artwork CRUD + public viewer routing."""

from django.test import Client


def test_unauthenticated_artworks_list_returns_401():
    """GET /artworks without a session must reject — confirms wiring + auth."""
    client = Client()
    response = client.get("/api/v1/artworks")
    assert response.status_code == 401


def test_unauthenticated_artworks_create_returns_401():
    client = Client()
    response = client.post(
        "/api/v1/artworks",
        data={
            "kind": "snapshot",
            "title": "probe",
            "thumbnail_data_url": "data:image/jpeg;base64,/9j/4A=",
        },
        content_type="application/json",
    )
    assert response.status_code == 401


def test_public_artworks_route_resolves_404_for_unknown_token():
    """Bad token → 404 (route resolved). Anything else → routing broken."""
    client = Client()
    response = client.get("/api/v1/public/artworks/this-token-does-not-exist")
    assert response.status_code == 404
