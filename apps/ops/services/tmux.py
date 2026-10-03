"""List / rename / kill sessions on the admin Terminal's tmux server.

The shells live in `x106-tmux.service` (see `terminal_ws/server.py`); the
WebSocket bridge only attaches clients. x106-api runs as root on the same box,
so it talks to the tmux socket directly — no RPC through the bridge.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import UTC, datetime

# Same default as terminal_ws/server.py — keep the two in sync.
SOCKET = os.environ.get("X106_TMUX_SOCKET", "/run/x106-console/tmux.sock")
TMUX_BIN = os.environ.get("TERMINAL_WS_TMUX", "/usr/bin/tmux")

SESSION_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")

_FIELDS = (
    "session_name",
    "session_attached",
    "session_created",
    "session_activity",
    "session_windows",
    "pane_current_command",
    "pane_current_path",
)
_FORMAT = "\t".join(f"#{{{f}}}" for f in _FIELDS)


class TmuxError(RuntimeError):
    """tmux missing, timed out, or refused the command."""


class SessionNotFound(LookupError):
    pass


class SessionExists(ValueError):
    pass


def valid_name(name: str) -> bool:
    return bool(SESSION_RE.fullmatch(name or ""))


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [TMUX_BIN, "-S", SOCKET, *args],
            capture_output=True,
            text=True,
            timeout=5,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as err:
        raise TmuxError("tmux is not installed") from err
    except subprocess.TimeoutExpired as err:
        raise TmuxError("tmux did not answer in 5s") from err


def _iso(epoch: str) -> str | None:
    try:
        return datetime.fromtimestamp(int(epoch), tz=UTC).isoformat()
    except (TypeError, ValueError):
        return None


def parse_sessions(out: str) -> list[dict]:
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != len(_FIELDS):
            continue
        name, attached, created, activity, windows, command, path = parts
        rows.append(
            {
                "name": name,
                "attached": int(attached or 0),
                "created_at": _iso(created),
                "activity_at": _iso(activity),
                "windows": int(windows or 0),
                "command": command,
                "path": path,
            }
        )
    rows.sort(key=lambda r: r["created_at"] or "")
    return rows


def list_sessions() -> list[dict]:
    """Sessions oldest-first. No server (nothing opened since boot) = []."""
    if not os.path.exists(SOCKET):
        return []
    proc = _run("list-sessions", "-F", _FORMAT)
    if proc.returncode != 0:
        # "no server running" / "error connecting" on a stale socket.
        return []
    return parse_sessions(proc.stdout)


def _raise_for(proc: subprocess.CompletedProcess[str], name: str) -> None:
    if proc.returncode == 0:
        return
    msg = (proc.stderr or proc.stdout).strip()
    if "can't find session" in msg or "no server running" in msg or "error connecting" in msg:
        raise SessionNotFound(name)
    if "duplicate session" in msg:
        raise SessionExists(name)
    raise TmuxError(msg or f"tmux exited {proc.returncode}")


def rename_session(old: str, new: str) -> None:
    # `=name` = exact match; without it tmux happily prefix-matches.
    _raise_for(_run("rename-session", "-t", f"={old}", new), old)


def kill_session(name: str) -> None:
    _raise_for(_run("kill-session", "-t", f"={name}"), name)
