"""DeepSeek streaming client + scene-call orchestration.

Mirrors internal/service/llm.go:callDeepSeek/doDeepSeekCall — same SSE parsing,
same idle watchdog (60s without a chunk → abort), same retry policy (one retry
on upstream errors with stricter prompt + lower temp).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx
from django.conf import settings

from ..errors import (
    LLMDisabledError,
    LLMOffError,
    LLMTimeoutError,
    LLMUpstreamError,
    SceneValidationError,
)
from ..models import LLMRequestLog
from ..settings_keys import effective_model, llm_enabled
from .prompts import build_system_prompt, build_user_prompt
from .scene import validate_and_clamp_scene

log = logging.getLogger("x106.studio.deepseek")

STREAM_IDLE_TIMEOUT = 60.0  # seconds
MAX_TOKENS = 16_384
LOG_TRUNCATE = 600


@dataclass
class _CallContext:
    user_id: str
    username: str
    mode: str
    attempt: int
    temperature: float
    model: str
    request_payload: dict
    response_raw: str = ""
    parsed_scene: dict | None = None
    status: str = ""
    error_message: str = ""
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


def _record_log(ctx: _CallContext) -> None:
    """Fire-and-forget. Logging must never break the upstream call."""
    try:
        LLMRequestLog.objects.create(
            user_id=ctx.user_id,
            username=ctx.username,
            mode=ctx.mode,
            model=ctx.model,
            attempt=ctx.attempt,
            temperature=Decimal(str(round(ctx.temperature, 2))),
            request_payload=ctx.request_payload,
            response_raw=ctx.response_raw[:LOG_TRUNCATE * 16] or None,
            parsed_direction=ctx.parsed_scene,
            status=ctx.status or "unknown",
            error_message=ctx.error_message or None,
            latency_ms=ctx.latency_ms,
            prompt_tokens=ctx.prompt_tokens,
            completion_tokens=ctx.completion_tokens,
            total_tokens=ctx.total_tokens,
        )
    except Exception as exc:  # pragma: no cover — best effort
        log.warning("could not record llm log: %s", exc)


def _stream_chunks(resp: httpx.Response):
    """Yield decoded chunk dicts from an SSE response.

    Tracks last-chunk time and raises LLMTimeoutError if no chunk arrives within
    STREAM_IDLE_TIMEOUT. Lines that aren't valid SSE are skipped (DeepSeek
    occasionally interleaves keepalive frames).
    """
    last_activity = time.monotonic()
    for raw_line in resp.iter_lines():
        now = time.monotonic()
        if now - last_activity > STREAM_IDLE_TIMEOUT:
            raise LLMTimeoutError(f"stream idle > {STREAM_IDLE_TIMEOUT}s")
        last_activity = now
        line = (raw_line or "").strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            yield json.loads(payload)
        except json.JSONDecodeError:
            continue


def _do_call(ctx: _CallContext, system_prompt: str, user_prompt: str) -> dict:
    headers = {
        "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    body: dict[str, Any] = {
        "model": ctx.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": MAX_TOKENS,
        "temperature": ctx.temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    ctx.request_payload = body

    start = time.monotonic()
    content_parts: list[str] = []
    finish_reason = ""
    chunk_count = 0
    upstream_error = ""

    try:
        with httpx.stream(
            "POST",
            f"{settings.DEEPSEEK_BASE_URL}/chat/completions",
            json=body,
            headers=headers,
            timeout=httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0),
        ) as resp:
            if resp.status_code < 200 or resp.status_code >= 300:
                raw = resp.read().decode("utf-8", errors="replace")
                ctx.response_raw = raw
                ctx.status = f"upstream_{resp.status_code}"
                ctx.error_message = raw[:LOG_TRUNCATE]
                ctx.latency_ms = int((time.monotonic() - start) * 1000)
                raise LLMUpstreamError(f"upstream status={resp.status_code}: {raw[:200]}")

            for chunk in _stream_chunks(resp):
                chunk_count += 1
                if isinstance(chunk.get("error"), dict):
                    upstream_error = chunk["error"].get("message", "")
                    break
                if isinstance(chunk.get("usage"), dict):
                    usage = chunk["usage"]
                    ctx.prompt_tokens = int(usage.get("prompt_tokens", 0))
                    ctx.completion_tokens = int(usage.get("completion_tokens", 0))
                    ctx.total_tokens = int(usage.get("total_tokens", 0))
                for choice in chunk.get("choices", []):
                    delta = (choice.get("delta") or {}).get("content")
                    if delta:
                        content_parts.append(delta)
                    fr = choice.get("finish_reason")
                    if fr:
                        finish_reason = fr
    except httpx.TimeoutException as exc:
        ctx.status = "timeout"
        ctx.error_message = str(exc)
        ctx.latency_ms = int((time.monotonic() - start) * 1000)
        raise LLMTimeoutError(str(exc)) from exc
    except httpx.HTTPError as exc:
        ctx.status = "http_error"
        ctx.error_message = str(exc)
        ctx.latency_ms = int((time.monotonic() - start) * 1000)
        raise LLMUpstreamError(f"http error: {exc}") from exc
    except LLMTimeoutError:
        ctx.status = "stream_idle_timeout"
        ctx.error_message = f"idle>{STREAM_IDLE_TIMEOUT}s after {chunk_count} chunks"
        ctx.latency_ms = int((time.monotonic() - start) * 1000)
        raise

    ctx.latency_ms = int((time.monotonic() - start) * 1000)

    if upstream_error:
        ctx.status = "upstream_error"
        ctx.error_message = upstream_error
        raise LLMUpstreamError(upstream_error)

    content = "".join(content_parts)
    ctx.response_raw = content
    if not content.strip():
        ctx.status = "empty_content"
        ctx.error_message = f"empty content; chunks={chunk_count} finish={finish_reason}"
        raise LLMUpstreamError("empty content")

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        ctx.status = "invalid_scene_json"
        ctx.error_message = str(exc)
        raise LLMUpstreamError(f"invalid scene json: {exc}") from exc

    try:
        scene = validate_and_clamp_scene(parsed)
    except SceneValidationError as exc:
        ctx.status = "validation_error"
        ctx.error_message = str(exc)
        raise LLMUpstreamError(str(exc)) from exc

    ctx.status = "success"
    ctx.parsed_scene = scene
    return scene


def call_deepseek(user_id: str, username: str, mode: str, current_scene: dict | None, stroke_count: int) -> dict:
    """Submit the prompt + retry once on upstream/parse errors."""
    if not settings.DEEPSEEK_API_KEY:
        raise LLMDisabledError()
    if not llm_enabled():
        raise LLMOffError()

    model = effective_model()
    system_prompt = build_system_prompt()
    user_prompt = build_user_prompt(mode, current_scene, stroke_count)

    ctx_first = _CallContext(
        user_id=user_id,
        username=username,
        mode=mode,
        attempt=1,
        temperature=0.9,
        model=model,
        request_payload={},
    )
    try:
        return _do_call(ctx_first, system_prompt, user_prompt)
    except LLMUpstreamError:
        retry_user_prompt = (
            user_prompt
            + "\n\nSTRICT JSON ONLY. Output đúng schema LLMScene v3. "
            "Mọi shape phải có đầy đủ: id, shape, color, material, motion, position[3], size[3], scale. Tối thiểu 4 shapes."
        )
        ctx_second = _CallContext(
            user_id=user_id,
            username=username,
            mode=mode,
            attempt=2,
            temperature=0.5,
            model=model,
            request_payload={},
        )
        try:
            return _do_call(ctx_second, system_prompt, retry_user_prompt)
        finally:
            _record_log(ctx_second)
    finally:
        _record_log(ctx_first)
