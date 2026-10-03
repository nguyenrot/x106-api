"""Snapshot of the VPS for the admin console's status panel: host metrics
(/proc), pm2 processes and the systemd units the ecosystem runs on.

x106-api runs as root on the box itself, so this reads /proc and shells out to
`pm2 jlist` / `systemctl show` directly. Every source degrades to `None`
instead of raising — a missing pm2 must not blank the whole panel.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

# Units shown in the panel, in display order. pm2 apps are discovered from
# `pm2 jlist`; systemd has no equivalent "ours" filter, so list them here.
# Add the unit when a new backend lands (same moment as the CLAUDE.md table).
SYSTEMD_UNITS: tuple[str, ...] = (
    "nginx",
    "mysql",
    "postgresql@17-main",
    "redis-server",
    "x106-api",
    "x106-celery-worker",
    "x106-celery-beat",
    "x106-terminal-ws",
    "x106-tmux",
    "lattice-api",
    "lattice-celery-worker",
    "lattice-celery-beat",
    "lumi-api",
    "lumi-celery-worker",
    "lumi-celery-beat",
    "mcp-api",
    "drive-api",
)

_PATH = "/root/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def _cmd(argv: list[str], timeout: float = 8) -> str | None:
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            env={**os.environ, "PATH": _PATH},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _iso_ms(ms: object) -> str | None:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=UTC).isoformat()  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None


# ─── /proc ────────────────────────────────────────────────────────────────


def _cpu_times() -> tuple[int, int] | None:
    try:
        with open("/proc/stat") as f:
            vals = [int(v) for v in f.readline().split()[1:]]
    except (OSError, ValueError):
        return None
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)  # idle + iowait
    return idle, sum(vals)


# Last /proc/stat sample of this worker process: (monotonic time, sample).
_last_cpu: tuple[float, tuple[int, int]] | None = None


def cpu_percent() -> float | None:
    """Busy % since this worker's previous call (the panel polls every ~10s).
    With no recent baseline, sample over 0.2s instead — callers must do that
    before spawning `pm2 jlist`, or node's own startup shows up as load."""
    global _last_cpu
    now, cur = time.monotonic(), _cpu_times()
    if cur is None:
        return None
    prev = _last_cpu
    if prev is None or not 1 <= now - prev[0] <= 120:
        time.sleep(0.2)
        prev, (now, cur) = (now, cur), (time.monotonic(), _cpu_times() or cur)
    _last_cpu = (now, cur)
    a, b = prev[1], cur
    if b[1] == a[1]:
        return None
    return round(100.0 * (1 - (b[0] - a[0]) / (b[1] - a[1])), 1)


def parse_meminfo(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            out[key] = int(parts[0]) * 1024  # kB → bytes
    return out


def host_metrics(cpu: float | None) -> dict:
    mem = None
    try:
        with open("/proc/meminfo") as f:
            info = parse_meminfo(f.read())
        mem = {
            "total": info.get("MemTotal"),
            "available": info.get("MemAvailable"),
            "swap_total": info.get("SwapTotal"),
            "swap_free": info.get("SwapFree"),
        }
    except OSError:
        pass
    uptime = None
    try:
        with open("/proc/uptime") as f:
            uptime = int(float(f.read().split()[0]))
    except (OSError, ValueError, IndexError):
        pass
    disk = shutil.disk_usage("/")
    return {
        "hostname": socket.gethostname(),
        "cpus": os.cpu_count(),
        "cpu_percent": cpu,
        "load": [round(x, 2) for x in os.getloadavg()],
        "uptime_sec": uptime,
        "memory": mem,
        "disk": {"total": disk.total, "used": disk.used, "free": disk.free},
    }


# ─── pm2 ──────────────────────────────────────────────────────────────────


def parse_pm2(raw: str) -> list[dict]:
    """Keep only what the panel shows — `pm2_env` also carries every env var
    of every app (secrets included), which must never leave this function."""
    start = raw.find("[")
    data = json.loads(raw[start:]) if start >= 0 else []
    rows = []
    for p in data:
        env = p.get("pm2_env") or {}
        monit = p.get("monit") or {}
        rows.append(
            {
                "name": p.get("name"),
                "status": env.get("status"),
                "pid": p.get("pid") or None,
                "cpu": monit.get("cpu"),
                "memory": monit.get("memory"),
                "restarts": env.get("restart_time", 0),
                "started_at": _iso_ms(env.get("pm_uptime")) if env.get("status") == "online" else None,
                "cwd": env.get("pm_cwd"),
            }
        )
    rows.sort(key=lambda r: r["name"] or "")
    return rows


def pm2_processes() -> list[dict] | None:
    raw = _cmd(["pm2", "jlist"])
    if raw is None:
        return None
    try:
        return parse_pm2(raw)
    except (ValueError, AttributeError):
        return None


# ─── systemd ──────────────────────────────────────────────────────────────


def _int_or_none(v: str | None) -> int | None:
    # MemoryCurrent is "[not set]" for inactive units and 2^64-1 for "infinity".
    if not v or not v.isdigit():
        return None
    n = int(v)
    return None if n >= 2**63 else n


def parse_systemctl_show(raw: str) -> list[dict]:
    rows = []
    for block in raw.strip().split("\n\n"):
        props = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if not props.get("Id") or props.get("LoadState") == "not-found":
            continue
        ts = props.get("ActiveEnterTimestamp", "")
        rows.append(
            {
                "name": props["Id"].removesuffix(".service"),
                "description": props.get("Description", ""),
                "active": props.get("ActiveState"),
                "sub": props.get("SubState"),
                "memory": _int_or_none(props.get("MemoryCurrent")),
                "restarts": _int_or_none(props.get("NRestarts")) or 0,
                "started_at": (
                    datetime.fromtimestamp(int(ts[1:]), tz=UTC).isoformat()
                    if ts.startswith("@") and ts[1:].isdigit() and props.get("ActiveState") == "active"
                    else None
                ),
            }
        )
    order = {name: i for i, name in enumerate(SYSTEMD_UNITS)}
    rows.sort(key=lambda r: order.get(r["name"], len(order)))
    return rows


def systemd_units() -> list[dict] | None:
    raw = _cmd(
        [
            "systemctl",
            "show",
            "--timestamp=unix",
            "-p",
            "Id,Description,LoadState,ActiveState,SubState,MemoryCurrent,ActiveEnterTimestamp,NRestarts",
            *[f"{u}.service" for u in SYSTEMD_UNITS],
        ]
    )
    return parse_systemctl_show(raw) if raw is not None else None


def overview() -> dict:
    cpu = cpu_percent()  # first, so it never measures the pm2 spawn below
    # `pm2 jlist` boots node (~0.3s); overlap it with systemctl.
    with ThreadPoolExecutor(max_workers=2) as pool:
        p, s = pool.submit(pm2_processes), pool.submit(systemd_units)
        return {
            "generated_at": datetime.now(tz=UTC).isoformat(),
            "host": host_metrics(cpu),
            "pm2": p.result(),
            "systemd": s.result(),
        }
