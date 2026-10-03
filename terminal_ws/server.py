"""WebSocket ↔ tmux bridge.

Each connection runs `tmux new-session -A -s <name>` in a fresh PTY, i.e. it
attaches a tmux *client* to a named session on the dedicated console tmux
server (`x106-tmux.service`, socket `TMUX_SOCKET`). The tmux server lives in its
own systemd unit, so shells survive a browser reload, a dropped connection and
— the important one — this daemon being restarted on every x106-api deploy.
Closing the WebSocket only detaches the client; the session keeps running.

Handshake: `GET /terminal/ws?s=<session>` — name `[A-Za-z0-9_-]{1,32}`,
default `main`, created on first attach.

Protocol (binary frames, first byte is opcode):

    client → server
        0x00 INPUT       payload = raw stdin bytes
        0x01 RESIZE      payload = UTF-8 JSON {"cols": N, "rows": M}
        0x02 PING        payload = (ignored)

    server → client
        0x00 OUTPUT      payload = raw stdout/stderr bytes from the PTY
        0x01 PONG        payload = (empty)
        0x02 EXIT        payload = optional UTF-8 reason

On attach the server waits (≤2s) for the client's first RESIZE so tmux starts
at the right size, sends a full reset, then replays the pane's scrollback
(`capture-pane`) so xterm.js scroll/search still see what happened before the
reload. EXIT is only sent when the session itself ended (shell exited / killed);
a plain disconnect just closes the socket and the client reattaches.

Auth happens once at handshake time: the daemon reads the `x106_admin`
cookie, verifies the HS256 JWT against `JWT_SECRET`, and rejects with HTTP
401 before the WebSocket is upgraded.
"""

from __future__ import annotations

import asyncio
import fcntl
import http
import json
import logging
import os
import pty
import re
import signal
import struct
import termios
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import jwt
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

# ─── Config (env-driven; systemd EnvironmentFile=/var/www/api/.env) ───────

JWT_SECRET = os.environ.get("JWT_SECRET", "x106-dev-secret-change-in-production")
LISTEN_HOST = os.environ.get("TERMINAL_WS_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("TERMINAL_WS_PORT", "7682"))
MAX_FRAME_BYTES = 1 << 20  # 1 MiB

TMUX_BIN = os.environ.get("TERMINAL_WS_TMUX", "/usr/bin/tmux")
# Same default as apps/ops/services/tmux.py — keep the two in sync.
TMUX_SOCKET = os.environ.get("X106_TMUX_SOCKET", "/run/x106-console/tmux.sock")
TMUX_CONF = os.environ.get("X106_TMUX_CONF", str(Path(__file__).with_name("tmux.conf")))
TMUX_UNIT = os.environ.get("X106_TMUX_UNIT", "x106-tmux.service")
START_DIR = os.environ.get("TERMINAL_WS_START_DIR", "/var/www")
HISTORY_LINES = 5000
FIRST_RESIZE_WAIT_SEC = 2.0

SESSION_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")
DEFAULT_SESSION = "main"

# ─── Protocol opcodes ─────────────────────────────────────────────────────

C_INPUT = 0x00
C_RESIZE = 0x01
C_PING = 0x02

S_OUTPUT = 0x00
S_PONG = 0x01
S_EXIT = 0x02

logger = logging.getLogger("x106.terminal_ws")


# ─── Auth ─────────────────────────────────────────────────────────────────


def _parse_cookie(cookie_header: str, name: str) -> str | None:
    if not cookie_header:
        return None
    for part in cookie_header.split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return None


def _verify_admin_jwt(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError as err:
        logger.info("jwt reject: %s", err)
        return None
    if payload.get("role") != "admin":
        logger.info("jwt reject: role=%r", payload.get("role"))
        return None
    return payload


def process_request(connection: ServerConnection, request: Request) -> Response | None:
    """Pre-upgrade hook — runs before the WebSocket handshake completes."""
    cookie = request.headers.get("Cookie", "")
    token = _parse_cookie(cookie, "x106_admin")
    peer = connection.remote_address
    if not token:
        logger.warning("auth fail: no x106_admin cookie from %s", peer)
        return connection.respond(http.HTTPStatus.UNAUTHORIZED, "no cookie\n")
    payload = _verify_admin_jwt(token)
    if payload is None:
        logger.warning("auth fail: bad token from %s", peer)
        return connection.respond(http.HTTPStatus.UNAUTHORIZED, "invalid token\n")

    query = parse_qs(urlsplit(request.path).query)
    session = (query.get("s") or [DEFAULT_SESSION])[0]
    if not SESSION_RE.fullmatch(session):
        return connection.respond(http.HTTPStatus.BAD_REQUEST, "bad session name\n")

    # Stash for the handler. websockets.ServerConnection doesn't restrict
    # attribute assignment so this is safe.
    connection.admin_payload = payload  # type: ignore[attr-defined]
    connection.session_name = session  # type: ignore[attr-defined]
    return None


# ─── tmux helpers ─────────────────────────────────────────────────────────


async def _run(*argv: str, timeout: float = 5.0) -> tuple[int, bytes, bytes]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return 127, b"", f"{argv[0]}: not found".encode()
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, b"", b"timeout"
    return proc.returncode or 0, out, err


async def _tmux(*args: str) -> tuple[int, bytes, bytes]:
    return await _run(TMUX_BIN, "-S", TMUX_SOCKET, *args)


async def _ensure_tmux_server() -> None:
    """Make sure the console tmux server is up *under its own systemd unit*.

    If the tmux client we exec below had to start the server itself, the server
    (and every shell in it) would land in this daemon's cgroup and die on the
    next `systemctl restart x106-terminal-ws` — exactly what tmux is here to
    prevent. So start it through systemd first; the client fallback only kicks
    in where there's no systemd (local dev)."""
    rc, _, _ = await _tmux("list-sessions")
    if rc == 0:
        return
    rc, _, err = await _run("systemctl", "start", TMUX_UNIT, timeout=15)
    if rc != 0:
        logger.warning("systemctl start %s failed: %s", TMUX_UNIT, err.decode(errors="replace").strip())
        return
    for _ in range(30):
        rc, _, _ = await _tmux("list-sessions")
        if rc == 0:
            return
        await asyncio.sleep(0.1)
    logger.warning("tmux server not reachable at %s after starting %s", TMUX_SOCKET, TMUX_UNIT)


async def _scrollback(session: str) -> bytes:
    """History above the visible screen of the session's active pane, ready to
    write into a freshly reset xterm. Empty for a session that doesn't exist yet."""
    rc, out, _ = await _tmux(
        "capture-pane",
        "-p",
        "-e",
        "-J",
        "-S",
        f"-{HISTORY_LINES}",
        "-E",
        "-1",
        "-t",
        f"={session}:",
    )
    if rc != 0:
        return b""
    lines = out.rstrip(b"\n")
    if not lines.strip():
        return b""
    return lines.replace(b"\n", b"\r\n") + b"\r\n"


# ─── PTY helpers ──────────────────────────────────────────────────────────


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    rows = max(1, min(rows, 1000))
    cols = max(1, min(cols, 1000))
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _parse_resize(body: bytes) -> tuple[int, int] | None:
    try:
        d = json.loads(body.decode("utf-8"))
        return int(d.get("rows", 24)), int(d.get("cols", 80))
    except (ValueError, TypeError, AttributeError):
        return None


def _spawn_attach(session: str, rows: int, cols: int) -> tuple[int, int]:
    """Fork a child running a tmux client attached to `session` (created if
    missing) on a fresh PTY. Returns (pid, master_fd)."""
    pid, fd = pty.fork()
    if pid == 0:
        # Child — size the PTY before tmux reads it, so the session doesn't
        # briefly reflow to 80×24 on every attach.
        try:
            _set_winsize(0, rows, cols)
        except OSError:
            pass
        env = {
            "TERM": "xterm-256color",
            "LANG": os.environ.get("LANG", "en_US.UTF-8"),
            "LC_ALL": os.environ.get("LC_ALL", "en_US.UTF-8"),
            "HOME": os.environ.get("HOME", "/root"),
            "USER": os.environ.get("USER", "root"),
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        }
        argv = [
            TMUX_BIN,
            "-u",
            "-S",
            TMUX_SOCKET,
            "-f",
            TMUX_CONF,
            "new-session",
            "-A",
            "-s",
            session,
            "-c",
            START_DIR,
        ]
        try:
            os.execve(TMUX_BIN, argv, env)
        except OSError:
            os._exit(127)
    return pid, fd


async def _reap(pid: int) -> None:
    """Detach the tmux client (SIGHUP; SIGKILL if it lingers) and reap it —
    no SIGCHLD=SIG_IGN here, it would break asyncio's subprocess return codes."""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGHUP, signal.SIGKILL):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass
        deadline = loop.time() + 2.0
        while True:
            try:
                wpid, _ = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                return
            if wpid:
                return
            if loop.time() >= deadline:
                break
            await asyncio.sleep(0.05)


# ─── Connection handler ───────────────────────────────────────────────────


async def handle(ws: ServerConnection) -> None:
    payload = getattr(ws, "admin_payload", {})
    session = getattr(ws, "session_name", DEFAULT_SESSION)
    user = payload.get("username") or payload.get("sub") or "?"
    peer = ws.remote_address
    logger.info("connect user=%s session=%s peer=%s", user, session, peer)

    # The client sends RESIZE right after open; wait for it so tmux attaches
    # at the real size. Anything else that arrives first is replayed below.
    rows, cols = 24, 80
    early: list[bytes] = []
    try:
        first = await asyncio.wait_for(ws.recv(), timeout=FIRST_RESIZE_WAIT_SEC)
    except TimeoutError:
        first = None
    except ConnectionClosed:
        return
    if isinstance(first, str):
        first = first.encode("utf-8")
    if first:
        size = _parse_resize(first[1:]) if first[0] == C_RESIZE else None
        if size:
            rows, cols = size
        else:
            early.append(first)

    await _ensure_tmux_server()
    history = await _scrollback(session)
    # RIS wipes whatever an earlier attach left in this xterm (reconnects reuse
    # the same instance), then the history, then a screenful of blank lines so
    # all of it scrolls into xterm's scrollback before tmux paints the screen.
    try:
        await ws.send(bytes([S_OUTPUT]) + b"\x1bc" + history + b"\r\n" * (rows if history else 0))
    except ConnectionClosed:
        return

    pid, fd = _spawn_attach(session, rows, cols)
    loop = asyncio.get_running_loop()
    output_queue: asyncio.Queue[bytes] = asyncio.Queue()
    closing = asyncio.Event()
    pty_eof = asyncio.Event()

    def _on_pty_readable() -> None:
        try:
            data = os.read(fd, 4096)
        except OSError:
            data = b""
        if not data:
            pty_eof.set()
            closing.set()
            try:
                loop.remove_reader(fd)
            except (OSError, ValueError):
                pass
            return
        output_queue.put_nowait(data)

    loop.add_reader(fd, _on_pty_readable)

    async def apply_frame(msg: bytes | str) -> None:
        if isinstance(msg, str):
            msg = msg.encode("utf-8")
        if not msg:
            return
        op, body = msg[0], msg[1:]
        if op == C_INPUT:
            try:
                os.write(fd, body)
            except OSError:
                closing.set()
        elif op == C_RESIZE:
            size = _parse_resize(body)
            if size:
                try:
                    _set_winsize(fd, *size)
                except OSError as err:
                    logger.debug("bad resize: %s", err)
        elif op == C_PING:
            await ws.send(bytes([S_PONG]))
        else:
            logger.debug("unknown opcode 0x%02x", op)

    async def pump_pty_to_ws() -> None:
        try:
            while not closing.is_set() or not output_queue.empty():
                try:
                    data = await asyncio.wait_for(output_queue.get(), timeout=0.25)
                except TimeoutError:
                    if closing.is_set():
                        break
                    continue
                await ws.send(bytes([S_OUTPUT]) + data)
        except ConnectionClosed:
            pass
        except Exception:
            logger.exception("pump_pty_to_ws crashed")

    async def pump_ws_to_pty() -> None:
        try:
            for msg in early:
                await apply_frame(msg)
            async for msg in ws:
                await apply_frame(msg)
                if closing.is_set():
                    return
        except ConnectionClosed:
            pass
        except Exception:
            logger.exception("pump_ws_to_pty crashed")
        finally:
            closing.set()

    pty_task = asyncio.create_task(pump_pty_to_ws())
    ws_task = asyncio.create_task(pump_ws_to_pty())

    done, pending = await asyncio.wait({pty_task, ws_task}, return_when=asyncio.FIRST_COMPLETED)
    closing.set()
    for t in pending:
        t.cancel()
    for t in pending:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass

    try:
        loop.remove_reader(fd)
    except (OSError, ValueError):
        pass

    # Browser went away → detach (SIGHUP), the session keeps running.
    # PTY hit EOF → the tmux client exited by itself: the session ended (shell
    # `exit`, kill-session) — or it was detached elsewhere. Only tell the
    # browser "ended" when the session is really gone, so it doesn't respawn a
    # fresh shell under the same name behind the user's back.
    await _reap(pid)
    try:
        os.close(fd)
    except OSError:
        pass

    if pty_eof.is_set():
        rc, _, _ = await _tmux("has-session", "-t", f"={session}")
        try:
            if rc != 0:
                await ws.send(bytes([S_EXIT]) + b"session ended")
            await ws.close()
        except ConnectionClosed:
            pass

    logger.info("disconnect user=%s session=%s peer=%s", user, session, peer)


# ─── Entry point ──────────────────────────────────────────────────────────


async def main() -> None:
    logging.basicConfig(
        level=os.environ.get("TERMINAL_WS_LOG", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger.info(
        "x106-terminal-ws listening on %s:%d (tmux socket=%s)",
        LISTEN_HOST,
        LISTEN_PORT,
        TMUX_SOCKET,
    )
    async with serve(
        handle,
        LISTEN_HOST,
        LISTEN_PORT,
        process_request=process_request,
        max_size=MAX_FRAME_BYTES,
        ping_interval=20,
        ping_timeout=20,
    ) as server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
