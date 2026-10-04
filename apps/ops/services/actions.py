"""Restart / reload / start a service from the admin console's Services page.

Only names the cockpit already knows are accepted — systemd units from
`host.SYSTEMD_UNITS`, pm2 apps from a fresh `pm2 jlist` — and everything is
exec'd as an argv list, never through a shell.

Two targets take down the request that asked for them, so they're fired
detached and reported as `async`: `x106-api` (this process) and the admin's own
pm2 app (the Nitro proxy waiting for this response).
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass

from . import host

logger = logging.getLogger("x106.ops.actions")

ACTIONS = ("restart", "reload", "start")

SELF_UNITS = frozenset({"x106-api"})
SELF_PM2 = frozenset({"admin-pkn"})

# Restarting these needs an explicit `force` — the reason is shown to the user.
GUARDED_UNITS = {
    "x106-tmux": "Restart x106-tmux giết mọi phiên terminal đang mở (kể cả agy đang chạy).",
}

_ENV = {**os.environ, "PATH": host._PATH, "HOME": "/root", "PM2_HOME": "/root/.pm2"}
_MAX_OUTPUT = 4000


class ActionError(Exception):
    """Refused or failed — `detail` is user-facing, `output` the command's tail."""

    def __init__(self, detail: str, output: str = "", status: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.output = output
        self.status = status


@dataclass
class ActionResult:
    output: str
    detached: bool


def _run(argv: list[str], timeout: float = 45) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, env=_ENV
        )
    except FileNotFoundError as err:
        raise ActionError(f"{argv[0]} không có trên máy.", status=503) from err
    except subprocess.TimeoutExpired as err:
        raise ActionError(f"{' '.join(argv)} chạy quá {int(timeout)}s.", status=504) from err
    out = (proc.stdout + proc.stderr).strip()
    return proc.returncode, out[-_MAX_OUTPUT:]


def _detach(argv: list[str]) -> None:
    """Fire `argv` one second from now in a transient systemd unit, outside this
    process' cgroup, so killing the caller doesn't kill the command."""
    rc, out = _run(
        [
            "systemd-run",
            "--collect",
            "--no-block",
            "--quiet",
            "--setenv=HOME=/root",
            "--setenv=PM2_HOME=/root/.pm2",
            f"--setenv=PATH={host._PATH}",
            "--",
            "/bin/sh",
            "-c",
            'sleep 1; exec "$@"',
            "sh",
            *argv,
        ],
        timeout=15,
    )
    if rc != 0:
        raise ActionError("Không tách được lệnh ra chạy nền.", out, status=500)


def _systemd(name: str, action: str, force: bool) -> ActionResult:
    if name not in host.SYSTEMD_UNITS:
        raise ActionError(f"'{name}' không nằm trong danh sách unit của console.", status=404)
    if action != "reload" and name in GUARDED_UNITS and not force:
        raise ActionError(GUARDED_UNITS[name], status=409)
    unit = f"{name}.service"

    if action == "reload":
        rc, out = _run(["systemctl", "show", "-p", "CanReload", "--value", unit], timeout=10)
        if out.strip() != "yes":
            raise ActionError(f"{name} không hỗ trợ reload — dùng restart.")
        if name == "nginx":
            rc, out = _run(["nginx", "-t"], timeout=15)
            if rc != 0:
                raise ActionError("nginx -t báo lỗi cấu hình — chưa reload.", out)

    if name in SELF_UNITS:
        _detach(["systemctl", action, unit])
        return ActionResult(output="", detached=True)

    rc, out = _run(["systemctl", action, unit])
    if rc != 0:
        _, status = _run(["systemctl", "status", unit, "--no-pager", "-n", "15"], timeout=10)
        raise ActionError(f"systemctl {action} {name} thất bại.", (out + "\n" + status).strip(), status=500)
    return ActionResult(output=out, detached=False)


def _pm2(name: str, action: str) -> ActionResult:
    procs = host.pm2_processes()
    if procs is None:
        raise ActionError("Không đọc được pm2 jlist.", status=503)
    if name not in {p["name"] for p in procs}:
        raise ActionError(f"pm2 không có app '{name}'.", status=404)

    if name in SELF_PM2:
        _detach(["pm2", action, name])
        return ActionResult(output="", detached=True)

    rc, out = _run(["pm2", action, name])
    if rc != 0:
        raise ActionError(f"pm2 {action} {name} thất bại.", out, status=500)
    return ActionResult(output=out, detached=False)


def run(kind: str, name: str, action: str, *, force: bool = False, user: str = "?") -> ActionResult:
    if action not in ACTIONS:
        raise ActionError(f"Hành động '{action}' không hỗ trợ.")
    if kind == "systemd":
        result = _systemd(name, action, force)
    elif kind == "pm2":
        result = _pm2(name, action)
    else:
        raise ActionError(f"Loại dịch vụ '{kind}' không hỗ trợ.")
    logger.warning(
        "ops action by %s: %s %s %s%s", user, kind, action, name, " (detached)" if result.detached else ""
    )
    return result
