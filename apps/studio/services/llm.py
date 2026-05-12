"""Multi-provider chat orchestration.

Chat is the only AI surface in the art studio. Each user turn:
  1. Flash model classifies intent (draw vs pure-chat). On any failure, fall
     through to step 2 — better to over-draw than swallow a draw request.
  2. Pro model generates the scene + reply. On upstream/parse failure, one
     retry with a stricter user-prompt suffix and lower temperature.

Both models route through `providers.call_provider()`. The pipeline is
provider-agnostic — DeepSeek native, OpenCode OpenAI-compat, and OpenCode
Anthropic-compat are all interchangeable here.

`call_llm()` is the public sync entry called by the Celery task. It accepts
optional `flash_model` / `pro_model` overrides (resolved per request); when
None it uses `effective_*_model()` from settings_keys.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal

from django.conf import settings

from ..errors import (
    LLMDisabledError,
    LLMOffError,
    LLMUpstreamError,
    SceneValidationError,
)
from ..models import LLMRequestLog
from .model_catalog import get_model
from .prompts import (
    CHAT_ROUTER_PROMPT,
    CHAT_SYSTEM_PROMPT,
    build_chat_user_prompt,
    build_router_user_prompt,
)
from .providers import (
    LOG_TRUNCATE,
    ProviderResult,
    call_provider,
)
from .scene import CHAT_MESSAGE_MAX_RUNES, validate_chat_response
from ..settings_keys import (
    effective_flash_model,
    effective_pro_model,
    llm_enabled,
)
from apps.core.text import clamp_runes

import json_repair

log = logging.getLogger("x106.studio.llm")

CHAT_MAX_TOKENS = 32000
ROUTER_MAX_TOKENS = 300


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


def _do_call(ctx: _CallContext, messages: list[dict], max_tokens: int) -> str:
    """Run one provider call. Returns raw content; updates ctx for logging."""
    spec = get_model(ctx.model)
    remote_model = spec.remote_id if spec else ctx.model
    # We only log the request shape (model + temperature) — the full prompt is
    # already represented by mode + ctx.mode and we don't want to bloat the row.
    ctx.request_payload = {
        "model": ctx.model,
        "remoteModel": remote_model,
        "provider": spec.provider if spec else "unknown",
        "max_tokens": max_tokens,
        "temperature": ctx.temperature,
    }

    start = time.monotonic()
    try:
        result: ProviderResult = call_provider(
            model_id=ctx.model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=ctx.temperature,
            response_format_json=True,
        )
    except Exception as exc:
        ctx.latency_ms = int((time.monotonic() - start) * 1000)
        # Carry across to log; status/error are set by caller if it wants more
        # detail.
        if not ctx.status:
            ctx.status = "upstream_error"
        if not ctx.error_message:
            ctx.error_message = str(exc)[:LOG_TRUNCATE]
        raise

    ctx.latency_ms = int((time.monotonic() - start) * 1000)
    ctx.prompt_tokens = result.prompt_tokens
    ctx.completion_tokens = result.completion_tokens
    ctx.total_tokens = result.total_tokens
    ctx.response_raw = result.content

    if not result.content.strip():
        ctx.status = "empty_content"
        ctx.error_message = f"empty content; chunks={result.chunk_count} finish={result.finish_reason}"
        raise LLMUpstreamError("empty content")
    return result.content


def _parse_and_validate_chat(content: str, ctx: _CallContext) -> tuple[dict | None, str]:
    """Parse JSON + validate as chat response. Mutates ctx for logging."""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as primary_exc:
        try:
            parsed = json_repair.loads(content)
            if not isinstance(parsed, dict):
                raise ValueError(f"json_repair returned {type(parsed).__name__}, expected dict")
            ctx.status = "repaired_json"
        except Exception:
            ctx.status = "invalid_chat_json"
            ctx.error_message = str(primary_exc)
            raise LLMUpstreamError(f"invalid chat json: {primary_exc}") from primary_exc
    try:
        scene, message = validate_chat_response(parsed)
    except SceneValidationError as exc:
        ctx.status = "validation_error"
        ctx.error_message = str(exc)
        raise LLMUpstreamError(str(exc)) from exc
    if ctx.status != "repaired_json":
        ctx.status = "success"
    ctx.parsed_scene = scene if scene is not None else {"message_only": True, "message": message}
    return (scene, message)


def _build_history_messages(history: list[dict] | None) -> list[dict]:
    out: list[dict] = []
    if not history:
        return out
    for turn in history[-4:]:
        role = turn.get("role")
        content = turn.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            out.append({"role": role, "content": content})
    return out


def _call_router_flash(
    *,
    user_id: str,
    username: str,
    user_message: str,
    history: list[dict] | None,
    has_scene: bool,
    flash_model: str,
) -> tuple[bool, str]:
    """Step 1 of hybrid chat. Returns (needs_scene, conversational_message).

    On any failure (timeout, malformed JSON, network, unconfigured provider),
    falls back to (True, "") so the caller routes to the heavy pro flow."""
    user_prompt = build_router_user_prompt(user_message, has_scene)
    history_messages = _build_history_messages(history)

    ctx = _CallContext(
        user_id=user_id, username=username, mode="chat-router",
        attempt=1, temperature=0.3, model=flash_model,
    )
    messages = (
        [{"role": "system", "content": CHAT_ROUTER_PROMPT}]
        + history_messages
        + [{"role": "user", "content": user_prompt}]
    )
    try:
        content = _do_call(ctx, messages, ROUTER_MAX_TOKENS)
    except Exception as exc:
        ctx.status = ctx.status or "router_call_failed"
        ctx.error_message = ctx.error_message or str(exc)
        log.warning("chat router flash call failed: %s — falling back to pro", exc)
        _record_log(ctx)
        return (True, "")

    try:
        parsed = json.loads(content)
        needs_scene = bool(parsed.get("needsScene"))
        message_raw = parsed.get("message") or ""
        message = (
            clamp_runes(message_raw.strip(), CHAT_MESSAGE_MAX_RUNES)
            if isinstance(message_raw, str) else ""
        )
        if not needs_scene and not message:
            needs_scene = True
        ctx.status = "success"
        ctx.parsed_scene = {"router_decision": "draw" if needs_scene else "chat", "message": message}
    except (json.JSONDecodeError, AttributeError, TypeError) as exc:
        ctx.status = "router_parse_failed"
        ctx.error_message = str(exc)
        log.warning("chat router flash parse failed: %s — falling back to pro", exc)
        _record_log(ctx)
        return (True, "")

    _record_log(ctx)
    return (needs_scene, message)


def _call_chat_pro(
    *,
    user_id: str,
    username: str,
    user_message: str,
    history: list[dict] | None,
    current_scene: dict | None,
    pro_model: str,
) -> tuple[dict | None, str]:
    """Step 2: pro model generates scene + reply. One retry on parse/upstream errors."""
    user_prompt = build_chat_user_prompt(user_message, current_scene)
    history_messages = _build_history_messages(history)

    ctx_first = _CallContext(
        user_id=user_id, username=username, mode="chat",
        attempt=1, temperature=0.7, model=pro_model,
    )
    messages_first = (
        [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
        + history_messages
        + [{"role": "user", "content": user_prompt}]
    )

    try:
        content = _do_call(ctx_first, messages_first, CHAT_MAX_TOKENS)
        return _parse_and_validate_chat(content, ctx_first)
    except LLMUpstreamError:
        retry_user_prompt = (
            user_prompt
            + '\n\nSTRICT JSON ONLY. Output: {"scene": ... | null, "message": "<≤200 chars, same language as userMessage>"}.'
            " Field 'message' is REQUIRED and must not be empty."
        )
        ctx_second = _CallContext(
            user_id=user_id, username=username, mode="chat",
            attempt=2, temperature=0.4, model=pro_model,
        )
        messages_second = (
            [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
            + history_messages
            + [{"role": "user", "content": retry_user_prompt}]
        )
        try:
            content = _do_call(ctx_second, messages_second, CHAT_MAX_TOKENS)
            return _parse_and_validate_chat(content, ctx_second)
        finally:
            _record_log(ctx_second)
    finally:
        _record_log(ctx_first)


def call_llm(
    *,
    user_id: str,
    username: str,
    user_message: str,
    current_scene: dict | None,
    history: list[dict] | None = None,
    flash_model: str | None = None,
    pro_model: str | None = None,
) -> tuple[dict | None, str]:
    """Public sync entry called by the Celery task. Returns (scene_or_none, message).

    `scene` is null when AI replied without changing the canvas. `message` is
    always present. `flash_model` / `pro_model` are catalog ids (e.g.
    "deepseek-v4-pro", "opencode-go/kimi-k2.6"); fall back to admin defaults
    when not supplied.
    """
    if not settings.DEEPSEEK_API_KEY and not settings.OPENCODE_API_KEY:
        # We accept either key — DeepSeek native or OpenCode. Only fail if both
        # are blank (no provider can run at all).
        raise LLMDisabledError()
    if not llm_enabled():
        raise LLMOffError()
    if not user_message:
        raise LLMUpstreamError("user_message is required")

    resolved_flash = flash_model or effective_flash_model()
    resolved_pro = pro_model or effective_pro_model()

    has_scene = bool(
        current_scene and isinstance(current_scene, dict) and current_scene.get("shapes")
    )
    needs_scene, router_message = _call_router_flash(
        user_id=user_id,
        username=username,
        user_message=user_message,
        history=history,
        has_scene=has_scene,
        flash_model=resolved_flash,
    )
    if not needs_scene:
        return (None, router_message)

    return _call_chat_pro(
        user_id=user_id,
        username=username,
        user_message=user_message,
        history=history,
        current_scene=current_scene,
        pro_model=resolved_pro,
    )


# Back-compat alias so older imports keep working through the rename.
call_deepseek = call_llm
