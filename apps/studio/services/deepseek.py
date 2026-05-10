"""DeepSeek streaming client + scene-call orchestration.

Uses the official `openai` SDK against DeepSeek's OpenAI-compatible endpoint.
Idle protection comes from httpx's `read` timeout (180s without any byte from
the upstream socket → ReadTimeout → APITimeoutError → LLMTimeoutError). One
retry on upstream errors with stricter prompt + lower temp. Audit log per call.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from decimal import Decimal

import httpx
from django.conf import settings
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

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

MAX_TOKENS = 6000
LOG_TRUNCATE = 600

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL,
            timeout=httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0),
            max_retries=0,
        )
    return _client


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


def _do_call(ctx: _CallContext, system_prompt: str, user_prompt: str) -> dict:
    params = {
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
    ctx.request_payload = params

    start = time.monotonic()
    content_parts: list[str] = []
    finish_reason = ""
    chunk_count = 0

    try:
        stream = _get_client().chat.completions.create(**params)
        for chunk in stream:
            chunk_count += 1

            usage = getattr(chunk, "usage", None)
            if usage is not None:
                ctx.prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
                ctx.completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
                ctx.total_tokens = int(getattr(usage, "total_tokens", 0) or 0)

            for choice in chunk.choices or []:
                delta = getattr(choice, "delta", None)
                if delta is not None and delta.content:
                    content_parts.append(delta.content)
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
    except APIStatusError as exc:
        body_text = ""
        if exc.body is not None:
            body_text = exc.body if isinstance(exc.body, str) else json.dumps(exc.body, default=str)
        ctx.status = f"upstream_{exc.status_code}"
        ctx.error_message = (exc.message or body_text)[:LOG_TRUNCATE]
        ctx.response_raw = body_text
        ctx.latency_ms = int((time.monotonic() - start) * 1000)
        raise LLMUpstreamError(f"upstream status={exc.status_code}: {body_text[:200]}") from exc
    except APITimeoutError as exc:
        ctx.status = "timeout"
        ctx.error_message = str(exc)
        ctx.latency_ms = int((time.monotonic() - start) * 1000)
        raise LLMTimeoutError(str(exc)) from exc
    except APIConnectionError as exc:
        ctx.status = "http_error"
        ctx.error_message = str(exc)
        ctx.latency_ms = int((time.monotonic() - start) * 1000)
        raise LLMUpstreamError(f"connection error: {exc}") from exc

    ctx.latency_ms = int((time.monotonic() - start) * 1000)

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
