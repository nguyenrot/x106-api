"""WebSocket ↔ PTY bridge.

Protocol (binary frames, first byte is opcode):

    client → server
        0x00 INPUT       payload = raw stdin bytes
        0x01 RESIZE      payload = UTF-8 JSON {"cols": N, "rows": M}
        0x02 PING        payload = (ignored)

    server → client
        0x00 OUTPUT      payload = raw stdout/stderr bytes from the PTY
        0x01 PONG        payload = (empty)
        0x02 EXIT        payload = optional UTF-8 reason

Auth happens once at handshake time: the daemon reads the `x106_admin`
cookie, verifies the HS256 JWT against `JWT_SECRET`, and rejects with HTTP
401/403 before the WebSocket is upgraded. Once accepted the shell process
inherits the daemon's UID (systemd pins it to `x106-ops`).
"""

from __future__ import annotations

import asyncio
import fcntl
import http
import json
import logging
import os
import pty
import signal
import struct
import termios

import jwt
from websockets.asyncio.server import ServerConnection, serve
from websockets.http11 import Request, Response

# ─── Config (env-driven; systemd EnvironmentFile=/var/www/api/.env) ───────

JWT_SECRET = os.environ.get("JWT_SECRET", "x106-dev-secret-change-in-production")
LISTEN_HOST = os.environ.get("TERMINAL_WS_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("TERMINAL_WS_PORT", "7682"))
SHELL_PATH = os.environ.get("TERMINAL_WS_SHELL", "/bin/bash")
MAX_FRAME_BYTES = 1 << 20  # 1 MiB

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
    # Stash for the handler. websockets.ServerConnection doesn't restrict
    # attribute assignment so this is safe.
    connection.admin_payload = payload  # type: ignore[attr-defined]
    return None


# ─── PTY helpers ──────────────────────────────────────────────────────────


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    rows = max(1, min(rows, 1000))
    cols = max(1, min(cols, 1000))
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _spawn_shell() -> tuple[int, int]:
    """Fork a child running `bash -l` attached to a fresh PTY. Returns (pid, master_fd)."""
    pid, fd = pty.fork()
    if pid == 0:
        # Child — start at filesystem root regardless of systemd WorkingDirectory.
        try:
            os.chdir("/")
        except OSError:
            pass
        env = {
            "TERM": "xterm-256color",
            "LANG": os.environ.get("LANG", "en_US.UTF-8"),
            "LC_ALL": os.environ.get("LC_ALL", "en_US.UTF-8"),
            "HOME": os.environ.get("HOME", "/root"),
            "USER": os.environ.get("USER", "root"),
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "SHELL": SHELL_PATH,
        }
        try:
            os.execvpe(SHELL_PATH, [SHELL_PATH, "-l"], env)
        except OSError:
            os._exit(127)
    return pid, fd


# ─── Connection handler ───────────────────────────────────────────────────


async def handle(ws: ServerConnection) -> None:
    payload = getattr(ws, "admin_payload", {})
    user = payload.get("username") or payload.get("sub") or "?"
    peer = ws.remote_address
    logger.info("connect user=%s peer=%s", user, peer)

    pid, fd = _spawn_shell()
    loop = asyncio.get_running_loop()
    output_queue: asyncio.Queue[bytes] = asyncio.Queue()
    closing = asyncio.Event()

    def _on_pty_readable() -> None:
        try:
            data = os.read(fd, 4096)
        except OSError:
            data = b""
        if not data:
            closing.set()
            try:
                loop.remove_reader(fd)
            except (OSError, ValueError):
                pass
            return
        output_queue.put_nowait(data)

    loop.add_reader(fd, _on_pty_readable)

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
        except Exception:
            logger.exception("pump_pty_to_ws crashed")

    async def pump_ws_to_pty() -> None:
        try:
            async for msg in ws:
                if isinstance(msg, str):
                    msg = msg.encode("utf-8")
                if not msg:
                    continue
                op = msg[0]
                body = msg[1:]
                if op == C_INPUT:
                    try:
                        os.write(fd, body)
                    except OSError:
                        closing.set()
                        return
                elif op == C_RESIZE:
                    try:
                        d = json.loads(body.decode("utf-8"))
                        _set_winsize(fd, int(d.get("rows", 24)), int(d.get("cols", 80)))
                    except (ValueError, KeyError, OSError) as err:
                        logger.debug("bad resize: %s", err)
                elif op == C_PING:
                    await ws.send(bytes([S_PONG]))
                else:
                    logger.debug("unknown opcode 0x%02x", op)
        except Exception:
            logger.exception("pump_ws_to_pty crashed")
        finally:
            closing.set()

    pty_task = asyncio.create_task(pump_pty_to_ws())
    ws_task = asyncio.create_task(pump_ws_to_pty())

    done, pending = await asyncio.wait(
        {pty_task, ws_task}, return_when=asyncio.FIRST_COMPLETED
    )
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

    try:
        os.kill(pid, signal.SIGHUP)
    except ProcessLookupError:
        pass
    try:
        # Reap the child so we don't leak zombies.
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass

    try:
        await ws.send(bytes([S_EXIT]))
    except Exception:
        pass

    logger.info("disconnect user=%s peer=%s", user, peer)


# ─── Entry point ──────────────────────────────────────────────────────────


async def main() -> None:
    logging.basicConfig(
        level=os.environ.get("TERMINAL_WS_LOG", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Reap any zombies that escape our explicit waitpid().
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)

    logger.info(
        "x106-terminal-ws listening on %s:%d (shell=%s)",
        LISTEN_HOST,
        LISTEN_PORT,
        SHELL_PATH,
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
