"""DeepSeek streaming client + chat-call orchestration.

Chat is the only AI surface in the art studio. We make a single v4-pro call
per user turn with multi-turn history; if the model returns malformed JSON or
the upstream errors, we retry once with a stricter user-prompt suffix.

httpx `read=180s` catches network idle at the socket level; OpenAI APITimeoutError
maps to LLMTimeoutError. `max_retries=0` on the SDK so our orchestration owns
all retry policy.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
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
from .prompts import CHAT_SYSTEM_PROMPT, build_chat_user_prompt
from .scene import validate_chat_response

log = logging.getLogger("x106.studio.deepseek")

CHAT_MAX_TOKENS = 4000  # chat scenes are smaller; cap output to keep latency tight
LOG_TRUNCATE = 600

_sync_client: OpenAI | None = None


def _get_sync_client() -> OpenAI:
    """Sync client is loop-agnostic — safe to cache process-wide."""
    global _sync_client
    if _sync_client is None:
        _sync_client = OpenAI(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL,
            timeout=httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0),
            max_retries=0,
        )
    return _sync_client


@dataclass
class _CallContext:
    user_id: str
    username: str
    mode: str
    attempt: int
    temperature: float
    model: str
    request_payload: dict = field(default_factory=dict)
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
            mode=ctx.mode[:16],
            model=ctx.model[:64],
            attempt=ctx.attempt,
            temperature=Decimal(str(round(ctx.temperature, 2))),
            request_payload=ctx.request_payload,
            response_raw=ctx.response_raw[:LOG_TRUNCATE * 16] or None,
            parsed_direction=ctx.parsed_scene,
            status=(ctx.status or "unknown")[:24],
            error_message=ctx.error_message or None,
            latency_ms=ctx.latency_ms,
            prompt_tokens=ctx.prompt_tokens,
            completion_tokens=ctx.completion_tokens,
            total_tokens=ctx.total_tokens,
        )
    except Exception as exc:  # pragma: no cover — best effort
        log.warning("could not record llm log: %s", exc)


def _do_call_sync(ctx: _CallContext, messages: list[dict], max_tokens: int) -> str:
    """Run one DeepSeek streaming call synchronously. Returns raw content; updates ctx.

    `messages` is the full OpenAI-style messages list (system + optional history + user).
    The caller is responsible for assembling it."""
    params = {
        "model": ctx.model,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
        "temperature": ctx.temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    ctx.request_payload = params

    start = time.monotonic()
    parts: list[str] = []
    finish_reason = ""
    chunk_count = 0

    try:
        stream = _get_sync_client().chat.completions.create(**params)
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
                    parts.append(delta.content)
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
    content = "".join(parts)
    ctx.response_raw = content
    if not content.strip():
        ctx.status = "empty_content"
        ctx.error_message = f"empty content; chunks={chunk_count} finish={finish_reason}"
        raise LLMUpstreamError("empty content")
    return content


def _parse_and_validate_chat(content: str, ctx: _CallContext) -> tuple[dict | None, str]:
    """Parse JSON + validate as chat response. Mutates ctx for logging."""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        ctx.status = "invalid_chat_json"
        ctx.error_message = str(exc)
        raise LLMUpstreamError(f"invalid chat json: {exc}") from exc
    try:
        scene, message = validate_chat_response(parsed)
    except SceneValidationError as exc:
        ctx.status = "validation_error"
        ctx.error_message = str(exc)
        raise LLMUpstreamError(str(exc)) from exc
    ctx.status = "success"
    ctx.parsed_scene = scene if scene is not None else {"message_only": True, "message": message}
    return (scene, message)


def _call_chat_sync(
    user_id: str,
    username: str,
    user_message: str,
    history: list[dict] | None,
    current_scene: dict | None,
) -> tuple[dict | None, str]:
    """Single-call chat with one retry on upstream/parse errors.

    Builds OpenAI messages = [system, ...history (≤4 turns), user]. Returns
    (scene_or_none, assistant_message). assistant_message is always present.
    """
    model = effective_model()
    user_prompt = build_chat_user_prompt(user_message, current_scene)

    history_messages: list[dict] = []
    if history:
        for turn in history[-4:]:
            role = turn.get("role")
            content = turn.get("content")
            if role in ("user", "assistant") and isinstance(content, str) and content.strip():
                history_messages.append({"role": role, "content": content})

    ctx_first = _CallContext(
        user_id=user_id, username=username, mode="chat",
        attempt=1, temperature=0.7, model=model,
    )

    messages_first = (
        [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
        + history_messages
        + [{"role": "user", "content": user_prompt}]
    )

    try:
        content = _do_call_sync(ctx_first, messages_first, CHAT_MAX_TOKENS)
        return _parse_and_validate_chat(content, ctx_first)
    except LLMUpstreamError:
        retry_user_prompt = (
            user_prompt
            + '\n\nSTRICT JSON ONLY. Output: {"scene": ... | null, "message": "<≤200 chars, same language as userMessage>"}.'
            " Field 'message' is REQUIRED and must not be empty."
        )
        ctx_second = _CallContext(
            user_id=user_id, username=username, mode="chat",
            attempt=2, temperature=0.4, model=model,
        )
        messages_second = (
            [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
            + history_messages
            + [{"role": "user", "content": retry_user_prompt}]
        )
        try:
            content = _do_call_sync(ctx_second, messages_second, CHAT_MAX_TOKENS)
            return _parse_and_validate_chat(content, ctx_second)
        finally:
            _record_log(ctx_second)
    finally:
        _record_log(ctx_first)


def call_deepseek(
    user_id: str,
    username: str,
    user_message: str,
    current_scene: dict | None,
    history: list[dict] | None = None,
) -> tuple[dict | None, str]:
    """Public sync entry called by the Celery task. Returns (scene_or_none, message).

    `scene` is null when AI replied without changing the canvas (clarifying
    question, off-topic steer-back, etc.). `message` is always present."""
    if not settings.DEEPSEEK_API_KEY:
        raise LLMDisabledError()
    if not llm_enabled():
        raise LLMOffError()
    if not user_message:
        raise LLMUpstreamError("user_message is required")
    return _call_chat_sync(user_id, username, user_message, history, current_scene)
