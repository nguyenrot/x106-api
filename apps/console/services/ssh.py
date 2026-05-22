"""Paramiko-based SSH executor for the VPS console.

A fresh connection per command — admin invocations are rare and bursty, and
the connection setup cost (~50ms over loopback) is dwarfed by the user-confirm
latency on the UI side. No pooling; trivial to reason about.

Configuration comes from `django.conf.settings`:
    CONSOLE_SSH_HOST, CONSOLE_SSH_PORT, CONSOLE_SSH_USER, CONSOLE_SSH_KEY_PATH

The private key is loaded once at import time and held in memory for the life
of the worker; the file on disk is read with mode 0600 (paramiko warns
otherwise — we leave the check to the OS).
"""

from __future__ import annotations

import logging
import socket
import time
from dataclasses import dataclass

import paramiko
from django.conf import settings

logger = logging.getLogger("x106.console.ssh")


class SSHConfigError(RuntimeError):
    """Raised when CONSOLE_SSH_* env vars are missing or invalid."""


@dataclass
class CommandResult:
    stdout: str
    stderr: str
    exit_code: int
    latency_ms: int
    timed_out: bool = False


def _load_pkey() -> paramiko.PKey:
    path = settings.CONSOLE_SSH_KEY_PATH
    if not path:
        raise SSHConfigError(
            "CONSOLE_SSH_KEY_PATH is not set; cannot SSH to the VPS"
        )
    # Try ed25519 first (what the docs prescribe), then fall back to RSA/ECDSA
    # so a different key type still works without code changes.
    last_err: Exception | None = None
    for loader in (
        paramiko.Ed25519Key.from_private_key_file,
        paramiko.ECDSAKey.from_private_key_file,
        paramiko.RSAKey.from_private_key_file,
    ):
        try:
            return loader(path)
        except (paramiko.SSHException, ValueError, OSError) as err:
            last_err = err
    raise SSHConfigError(
        f"Could not load SSH key at {path!r}: {last_err!r}"
    )


def run_command(command: str, timeout_sec: int) -> CommandResult:
    """Run `command` on the configured SSH host. Captures stdout, stderr,
    exit code, and latency. On timeout, returns `timed_out=True` and
    `exit_code = -1`."""
    pkey = _load_pkey()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    started = time.monotonic()
    timed_out = False
    stdout_data = b""
    stderr_data = b""
    exit_code = -1

    try:
        client.connect(
            hostname=settings.CONSOLE_SSH_HOST,
            port=settings.CONSOLE_SSH_PORT,
            username=settings.CONSOLE_SSH_USER,
            pkey=pkey,
            timeout=10,
            banner_timeout=10,
            auth_timeout=10,
            allow_agent=False,
            look_for_keys=False,
        )
        transport = client.get_transport()
        if transport is None:
            raise SSHConfigError("SSH transport failed to initialize")

        channel = transport.open_session()
        channel.settimeout(timeout_sec)
        channel.exec_command(command)

        try:
            # Read until EOF or timeout. paramiko channels raise socket.timeout
            # when settimeout() fires and there's no data.
            while True:
                if channel.recv_ready():
                    stdout_data += channel.recv(65536)
                if channel.recv_stderr_ready():
                    stderr_data += channel.recv_stderr(65536)
                if channel.exit_status_ready():
                    # Drain any final buffered output.
                    while channel.recv_ready():
                        stdout_data += channel.recv(65536)
                    while channel.recv_stderr_ready():
                        stderr_data += channel.recv_stderr(65536)
                    exit_code = channel.recv_exit_status()
                    break
                # Honor wall-clock budget regardless of paramiko's own
                # socket timer (it only fires on quiet sockets).
                if time.monotonic() - started > timeout_sec:
                    timed_out = True
                    break
                time.sleep(0.05)
        except socket.timeout:
            timed_out = True
        finally:
            try:
                channel.close()
            except Exception:
                pass
    except SSHConfigError:
        raise
    except Exception as err:
        # Any auth/network error → surface as stderr with -1 exit.
        stderr_data += f"\n[ssh-error] {err!r}".encode()
        logger.exception("SSH command failed: %r", command)
    finally:
        try:
            client.close()
        except Exception:
            pass

    latency_ms = int((time.monotonic() - started) * 1000)
    return CommandResult(
        stdout=stdout_data.decode("utf-8", errors="replace"),
        stderr=stderr_data.decode("utf-8", errors="replace"),
        exit_code=exit_code,
        latency_ms=latency_ms,
        timed_out=timed_out,
    )
