"""Admin ops cockpit — parsers for tmux / pm2 / systemctl output, and the
endpoints' auth + validation. Nothing here touches a real tmux or pm2: the
parsers take captured output, the endpoints get their service calls patched."""

from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client
from rest_framework_simplejwt.tokens import AccessToken

from apps.ops.services import host, tmux

User = get_user_model()


@pytest.fixture
def admin_client() -> Client:
    user = User.objects.create_user(username="ops-admin", password="x", is_staff=True)
    token = AccessToken.for_user(user)
    token["role"] = "admin"
    return Client(HTTP_AUTHORIZATION=f"Bearer {token}")


@pytest.fixture(autouse=True)
def _fresh_cache():
    cache.clear()


# ─── parsers ──────────────────────────────────────────────────────────────


def test_parse_tmux_sessions_sorts_by_creation_and_types_fields():
    out = (
        "logs\t0\t1791040400\t1791040500\t1\tjournalctl\t/var/www\n"
        "main\t2\t1791040298\t1791040600\t1\tzsh\t/var/www/api\n"
        "garbage line without tabs\n"
    )
    rows = tmux.parse_sessions(out)
    assert [r["name"] for r in rows] == ["main", "logs"]
    assert rows[0]["attached"] == 2
    assert rows[0]["command"] == "zsh"
    assert rows[0]["path"] == "/var/www/api"
    assert rows[0]["created_at"].startswith("2026-")


@pytest.mark.parametrize("name", ["main", "sh-2", "logs_x106", "A" * 32])
def test_valid_session_names(name):
    assert tmux.valid_name(name)


@pytest.mark.parametrize("name", ["", "a.b", "a:b", "a b", "=main", "A" * 33, "$(id)"])
def test_invalid_session_names(name):
    assert not tmux.valid_name(name)


def test_parse_pm2_drops_env_and_keeps_panel_fields():
    raw = "some pm2 banner\n" + json.dumps(
        [
            {
                "name": "vibe-hub",
                "pid": 123,
                "monit": {"memory": 141_000_000, "cpu": 1.5},
                "pm2_env": {
                    "status": "online",
                    "pm_uptime": 1787985452638,
                    "restart_time": 3,
                    "pm_cwd": "/var/www/hub",
                    "JWT_SECRET": "must-not-leak",
                },
            },
            {"name": "admin-pkn", "pid": 0, "monit": {}, "pm2_env": {"status": "stopped"}},
        ]
    )
    rows = host.parse_pm2(raw)
    assert [r["name"] for r in rows] == ["admin-pkn", "vibe-hub"]
    assert rows[1]["restarts"] == 3 and rows[1]["started_at"]
    assert rows[0]["pid"] is None and rows[0]["started_at"] is None
    assert "must-not-leak" not in json.dumps(rows)


def test_parse_systemctl_show_skips_missing_units_and_orders_like_registry():
    raw = (
        "NRestarts=0\nMemoryCurrent=415080448\nId=x106-api.service\nDescription=X106 API\n"
        "LoadState=loaded\nActiveState=active\nSubState=running\nActiveEnterTimestamp=@1790740980\n\n"
        "NRestarts=0\nMemoryCurrent=[not set]\nId=nope.service\nDescription=nope.service\n"
        "LoadState=not-found\nActiveState=inactive\nSubState=dead\nActiveEnterTimestamp=\n\n"
        "NRestarts=2\nMemoryCurrent=18446744073709551615\nId=nginx.service\nDescription=nginx\n"
        "LoadState=loaded\nActiveState=failed\nSubState=failed\nActiveEnterTimestamp=@1790740979\n"
    )
    rows = host.parse_systemctl_show(raw)
    assert [r["name"] for r in rows] == ["nginx", "x106-api"]
    assert rows[0]["memory"] is None and rows[0]["restarts"] == 2 and rows[0]["started_at"] is None
    assert rows[1]["memory"] == 415080448 and rows[1]["started_at"].startswith("2026-")


def test_parse_meminfo_converts_kb_to_bytes():
    info = host.parse_meminfo("MemTotal:       8000 kB\nMemAvailable:   2000 kB\nHugePages_Total: 0\n")
    assert info["MemTotal"] == 8000 * 1024
    assert info["MemAvailable"] == 2000 * 1024


# ─── endpoints ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path", ["/api/v1/admin/ops/overview", "/api/v1/admin/ops/terminals", "/api/v1/admin/ops/snippets"]
)
def test_ops_endpoints_require_admin(path):
    assert Client().get(path).status_code == 401


def test_overview_is_cached(admin_client, monkeypatch):
    calls = []
    monkeypatch.setattr(host, "overview", lambda: calls.append(1) or {"host": {}, "pm2": [], "systemd": []})
    assert admin_client.get("/api/v1/admin/ops/overview").status_code == 200
    assert admin_client.get("/api/v1/admin/ops/overview").status_code == 200
    assert len(calls) == 1


def test_terminal_list(admin_client, monkeypatch):
    monkeypatch.setattr(tmux, "list_sessions", lambda: [{"name": "main"}])
    res = admin_client.get("/api/v1/admin/ops/terminals")
    assert res.status_code == 200
    assert res.json() == {"sessions": [{"name": "main"}]}


def test_terminal_rename_validates_and_maps_errors(admin_client, monkeypatch):
    def rename(old, new):
        if old == "ghost":
            raise tmux.SessionNotFound(old)
        if new == "taken":
            raise tmux.SessionExists(new)

    monkeypatch.setattr(tmux, "rename_session", rename)
    patch = lambda name, body: admin_client.patch(  # noqa: E731
        f"/api/v1/admin/ops/terminals/{name}", data=json.dumps(body), content_type="application/json"
    )
    assert patch("main", {"name": "work"}).json() == {"name": "work"}
    assert patch("main", {"name": "bad name"}).status_code == 400
    assert patch("ghost", {"name": "work"}).status_code == 404
    assert patch("main", {"name": "taken"}).status_code == 400


def test_terminal_kill_is_idempotent(admin_client, monkeypatch):
    def kill(name):
        raise tmux.SessionNotFound(name)

    monkeypatch.setattr(tmux, "kill_session", kill)
    assert admin_client.delete("/api/v1/admin/ops/terminals/main").status_code == 204


def test_snippets_roundtrip_and_validation(admin_client):
    url = "/api/v1/admin/ops/snippets"
    assert admin_client.get(url).json() == {"items": []}

    items = [{"label": "pm2 list", "command": "pm2 list"}, {"label": "disk", "command": "df -h /"}]
    res = admin_client.put(url, data=json.dumps({"items": items}), content_type="application/json")
    assert res.status_code == 200
    assert admin_client.get(url).json() == {"items": items}

    bad = [{"label": "empty", "command": "   "}]
    res = admin_client.put(url, data=json.dumps({"items": bad}), content_type="application/json")
    assert res.status_code == 400
    assert admin_client.get(url).json() == {"items": items}
