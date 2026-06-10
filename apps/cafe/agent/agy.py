"""Invoke the Antigravity (`agy`) CLI and parse its JSON output.

Ported from lattice's apps/agent/agy.py (itself ported from the x106
quotes-agent) — thin and process-based, the CLI is the contract. The invocation
is overridable via env vars so it can be tuned without touching code:

  AGY_BIN         binary name or path        (default: "agy")
  AGY_ARGS        extra args, space-split     (default: "--print --dangerously-skip-permissions")
  AGY_INPUT_MODE  "stdin" | "args"            (default: "args")
                     stdin: prompt piped to agy; args: <extra> --prompt "<text>"

The celery worker runs under systemd with a stripped PATH, so we prepend the
standard uv/agy install dirs (`/root/.local/bin`, `~/.local/bin`) to the
subprocess PATH — otherwise `agy` is not found.

The prompt MUST instruct the LLM to emit a single JSON object (the system
prompt enforces this); we strip ```json fences defensively.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass

log = logging.getLogger("apps.cafe.agent.agy")


class AgyError(RuntimeError):
    def __init__(self, message: str, *, raw_output: str = "", duration_ms: int = 0):
        super().__init__(message)
        self.raw_output = raw_output
        self.duration_ms = duration_ms


@dataclass
class AgyResult:
    """All artifacts of one agy invocation — written verbatim to the run row."""

    parsed: dict
    raw_output: str
    duration_ms: int


_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(.*?)\n```\s*$", flags=re.DOTALL)


def _subprocess_env() -> dict[str, str]:
    """Copy the current env but guarantee agy/uv install dirs are on PATH."""
    env = dict(os.environ)
    extra = [
        os.path.expanduser("~/.local/bin"),
        "/root/.local/bin",
        "/usr/local/bin",
    ]
    current = env.get("PATH", "")
    parts = current.split(os.pathsep) if current else []
    for p in extra:
        if p and p not in parts:
            parts.append(p)
    env["PATH"] = os.pathsep.join(parts)
    return env


def _invoke_agy(prompt: str, *, timeout_sec: int) -> tuple[str, int]:
    """Shell out to `agy` once. Return (raw_stdout, duration_ms)."""
    binary = os.environ.get("AGY_BIN", "agy")
    extra = os.environ.get("AGY_ARGS", "--print --dangerously-skip-permissions")
    input_mode = os.environ.get("AGY_INPUT_MODE", "args").lower()

    extra_argv = shlex.split(extra) if extra else []

    if input_mode == "args":
        argv = [binary, *extra_argv, "--prompt", prompt]
        stdin_data = None
    else:
        argv = [binary, *extra_argv]
        stdin_data = prompt

    log.info("invoking agy: %s", argv[0])
    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
            env=_subprocess_env(),
        )
    except FileNotFoundError as e:
        raise AgyError(
            f"agy binary not found ({binary}). Install Antigravity CLI or set AGY_BIN."
        ) from e
    except subprocess.TimeoutExpired as e:
        raise AgyError(
            f"agy timed out after {timeout_sec}s",
            duration_ms=int((time.monotonic() - started) * 1000),
        ) from e

    duration_ms = int((time.monotonic() - started) * 1000)
    raw = completed.stdout or ""

    if completed.returncode != 0:
        tail = (completed.stderr or raw or "")[-1000:]
        raise AgyError(
            f"agy exit {completed.returncode}: {tail.strip() or '(no stderr)'}",
            raw_output=raw,
            duration_ms=duration_ms,
        )

    if not raw.strip():
        raise AgyError("agy produced empty stdout", raw_output=raw, duration_ms=duration_ms)

    return raw, duration_ms


def run_agy(prompt: str, *, timeout_sec: int = 300) -> AgyResult:
    """Run `agy` with `prompt`. Return parsed JSON + raw stdout + duration."""
    raw, duration_ms = _invoke_agy(prompt, timeout_sec=timeout_sec)
    parsed = _parse_json(raw.strip())
    return AgyResult(parsed=parsed, raw_output=raw, duration_ms=duration_ms)


def _parse_json(raw: str) -> dict:
    m = _FENCE_RE.match(raw.strip())
    if m:
        raw = m.group(1)
    # Find the first balanced JSON object if there's chatter around it.
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise AgyError(f"no JSON object in output: {raw[:300]!r}")
    candidate = raw[start : end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        raise AgyError(f"agy output not valid JSON: {e}; raw={candidate[:300]!r}") from e
