"""Run the `agy` Antigravity CLI as a subprocess and return its text output.

The backend systemd units (`x106-api`, `x106-celery-worker`) run as root on
the same VPS where agy is installed (`/root/.local/bin/agy`, OAuth token at
`/root/.gemini/antigravity-cli/`), so we drive it with `subprocess.run` —
no SSH layer needed.

agy in `--print` mode is **stateless**: passing a `--conversation <id>` for
an unknown UUID still answers but doesn't recall context across calls. We
therefore concatenate the recent ConsoleMessage history into a single prompt
on every turn; agy handles tool use (file reads, shell, code) autonomously
because `~/.gemini/antigravity-cli/settings.json` already sets
`"toolPermission": "always-proceed"`.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass

logger = logging.getLogger("x106.console.agy")

_AGY_BIN = "/root/.local/bin/agy"
_AGY_PRINT_TIMEOUT = "3m30s"  # agy's own watchdog; must be < Celery soft_time_limit
_SUBPROCESS_TIMEOUT_SEC = 240  # outer kill in case agy ignores its --print-timeout


class AgyError(RuntimeError):
    """agy missing, returned non-zero, or its wall-clock timeout fired."""


@dataclass
class AgyResult:
    text: str
    duration_ms: int


def run_agy(prompt: str) -> AgyResult:
    """Send `prompt` to agy --print and return the assistant text.

    Caller is responsible for assembling `prompt` from system instructions +
    chat history + the new user turn (see `apps.console.tasks._build_prompt`).
    """
    if not shutil.which(_AGY_BIN):
        raise AgyError(f"agy binary not found at {_AGY_BIN}")

    import time
    started = time.monotonic()
    try:
        cp = subprocess.run(
            [_AGY_BIN, "--print", "--print-timeout", _AGY_PRINT_TIMEOUT, prompt],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_SEC,
            check=False,
        )
    except subprocess.TimeoutExpired as err:
        raise AgyError(f"agy hung past {_SUBPROCESS_TIMEOUT_SEC}s and was killed") from err
    except FileNotFoundError as err:
        raise AgyError(f"agy binary missing: {err}") from err

    duration_ms = int((time.monotonic() - started) * 1000)

    if cp.returncode != 0:
        snippet = (cp.stderr or cp.stdout or "").strip()[:600]
        raise AgyError(f"agy exit {cp.returncode}: {snippet}")

    text = (cp.stdout or "").strip()
    if not text:
        raise AgyError("agy returned empty output")

    return AgyResult(text=text, duration_ms=duration_ms)
