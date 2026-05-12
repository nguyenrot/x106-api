"""Multi-provider chat orchestration.

Chat is the only AI surface in the art studio. Each user turn:
  1. Flash model classifies intent (draw vs pure-chat). On any failure, fall
     through to step 2 — better to over-draw than swallow a draw request.
  2. Pro model generates the scene + reply. On upstream/parse failure, one
     semantic retry with a stricter user-prompt suffix and lower temperature.

Both models route through `providers.call_provider()`. The pipeline is
provider-agnostic — DeepSeek native, OpenCode OpenAI-compat, and OpenCode
Anthropic-compat are all interchangeable here.

`_do_call` wraps every provider call with a 3-attempt exponential-backoff
retry for transient upstream errors (429/5xx/timeout/connection-reset). The
retry is invisible to callers but each attempt records its own LLMRequestLog
row so admin can see "this job took 3 attempts" in the audit trail.

`call_llm()` is the public sync entry called by the Celery task. It accepts
optional `flash_model` / `pro_model` overrides (resolved per request); when
None it uses `effective_*_model()` from settings_keys. `job_id` is optional
and threaded through so `_call_chat_pro` can check cancellation status
between the router and pro stages.
"""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass, field
from decimal import Decimal

import json_repair
from django.conf import settings

from apps.core.text import clamp_runes

from ..errors import (
    LLMDisabledError,
    LLMOffError,
    LLMTimeoutError,
    LLMUpstreamError,
    SceneValidationError,
)
from ..models import LLMJob, LLMJobStatus, LLMRequestLog
from ..settings_keys import (
    effective_flash_max_tokens,
    effective_flash_model,
    effective_pro_max_tokens,
    effective_pro_model,
    llm_enabled,
)
from .model_catalog import compute_cost_cents, get_model
from .prompts import (
    build_chat_user_prompt,
    build_router_user_prompt,
    get_active_prompt,
)
from .providers import (
    LOG_TRUNCATE,
    ProviderResult,
    call_provider,
)
from .scene import CHAT_MESSAGE_MAX_RUNES, validate_chat_response

log = logging.getLogger("x106.studio.llm")

# Retry policy for transient upstream errors (Phase 1.3).
RETRY_MAX_ATTEMPTS = 3
RETRY_BASE_DELAYS = (1.0, 3.0, 9.0)  # one per attempt index (0 → no delay, 1 → 1s, 2 → 3s, etc.)
RETRY_JITTER = 0.3  # ±30% multiplicative jitter
RETRY_CUMULATIVE_CAP_SECONDS = 25.0  # total sleep budget across one _do_call


class CanceledMidJob(Exception):
    """Raised when the worker notices the job row flipped to CANCELED between
    the router and pro stages. Worker turns this into a clean termination."""


@dataclass
class _CallContext:
    user_id: str
    username: str
    mode: str
    attempt: int
    temperature: float
    model: str
    prompt_version_id: int | None = None
    request_payload: dict = field(default_factory=dict)
    response_raw: str = ""
    parsed_scene: dict | None = None
    status: str = ""
    error_message: str = ""
    http_status: int | None = None
    cost_cents: int | None = None
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
            http_status=ctx.http_status,
            prompt_version_id=ctx.prompt_version_id,
            cost_cents=ctx.cost_cents,
        )
    except Exception as exc:  # pragma: no cover — best effort
        log.warning("could not record llm log: %s", exc)


def _is_retryable(exc: BaseException) -> bool:
    """Classify which upstream exceptions should be retried with backoff."""
    if isinstance(exc, LLMTimeoutError):
        return True
    if isinstance(exc, LLMUpstreamError):
        # Retryable status codes from the upstream provider. 4xx (other than
        # 408/425/429) is a client-side problem we can't fix by retrying.
        status_code = getattr(exc, "http_status", None)
        if status_code is None:
            # No status attached → either a connection error or a non-HTTP
            # upstream failure (e.g. timeout deeper in httpx). Treat as
            # transient — they usually clear on retry.
            return True
        return status_code in {408, 425, 429, 500, 502, 503, 504}
    return False


def _retry_sleep_for(attempt_index: int) -> float:
    """Backoff delay before attempt N (1-based). attempt_index=1 → 0s (no delay
    before first attempt), 2 → ~1s, 3 → ~3s."""
    if attempt_index <= 1:
        return 0.0
    base = RETRY_BASE_DELAYS[min(attempt_index - 2, len(RETRY_BASE_DELAYS) - 1)]
    jitter = 1.0 + random.uniform(-RETRY_JITTER, RETRY_JITTER)
    return max(base * jitter, 0.0)


def _do_call(ctx: _CallContext, messages: list[dict], max_tokens: int) -> str:
    """Run one provider call with retry-on-transient-error.

    Returns raw content on success. Updates ctx for logging. Raises the
    final exception after all attempts are exhausted.

    NOTE: Caller is responsible for `_record_log(ctx)` on success. On retry
    failures we record one log row per attempt so admin can see attempt count
    + per-attempt error in the audit trail.
    """
    spec = get_model(ctx.model)
    remote_model = spec.remote_id if spec else ctx.model
    ctx.request_payload = {
        "model": ctx.model,
        "remoteModel": remote_model,
        "provider": spec.provider if spec else "unknown",
        "max_tokens": max_tokens,
        "temperature": ctx.temperature,
    }

    cumulative_sleep = 0.0
    last_exc: BaseException | None = None

    for attempt_index in range(1, RETRY_MAX_ATTEMPTS + 1):
        # Sleep before attempts 2+. Capped so we don't burn the soft time limit.
        delay = _retry_sleep_for(attempt_index)
        if delay > 0:
            if cumulative_sleep + delay > RETRY_CUMULATIVE_CAP_SECONDS:
                log.warning(
                    "retry budget exhausted before attempt %d (cumulative=%.1fs)",
                    attempt_index, cumulative_sleep,
                )
                break
            time.sleep(delay)
            cumulative_sleep += delay

        # New row per attempt — except for the first, where the caller will
        # record using ctx as-is. We mutate ctx in place so per-attempt fields
        # (latency, tokens, error) reflect this attempt.
        ctx.status = ""
        ctx.error_message = ""
        ctx.response_raw = ""
        ctx.parsed_scene = None
        ctx.http_status = None
        ctx.cost_cents = None
        ctx.latency_ms = 0
        ctx.prompt_tokens = ctx.completion_tokens = ctx.total_tokens = 0

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
            ctx.http_status = getattr(exc, "http_status", None)
            ctx.status = "upstream_error" if isinstance(exc, LLMUpstreamError) else "upstream_timeout"
            ctx.error_message = str(exc)[:LOG_TRUNCATE]
            last_exc = exc

            if attempt_index < RETRY_MAX_ATTEMPTS and _is_retryable(exc):
                # Record this failed attempt and loop. We snapshot the ctx so
                # the next iteration gets a fresh slate while keeping the
                # earlier attempt's row in the audit trail.
                log.info(
                    "retrying upstream call attempt=%d/%d after %s: %s",
                    attempt_index, RETRY_MAX_ATTEMPTS, type(exc).__name__, exc,
                )
                _record_log(ctx)
                # Bump attempt for the next iteration's log row.
                ctx.attempt += 1
                continue
            # Last attempt, or non-retryable — bubble out. Caller records.
            raise

        ctx.latency_ms = int((time.monotonic() - start) * 1000)
        ctx.http_status = result.http_status
        ctx.prompt_tokens = result.prompt_tokens
        ctx.completion_tokens = result.completion_tokens
        ctx.total_tokens = result.total_tokens
        ctx.response_raw = result.content
        ctx.cost_cents = compute_cost_cents(
            ctx.model, ctx.prompt_tokens, ctx.completion_tokens
        )

        if not result.content.strip():
            ctx.status = "empty_content"
            ctx.error_message = (
                f"empty content; chunks={result.chunk_count} finish={result.finish_reason}"
            )
            # Empty content is treated as upstream failure; retry once if budget allows.
            empty_exc = LLMUpstreamError("empty content")
            last_exc = empty_exc
            if attempt_index < RETRY_MAX_ATTEMPTS:
                _record_log(ctx)
                ctx.attempt += 1
                continue
            raise empty_exc

        return result.content

    # Should not reach here unless retry budget was exhausted without raising.
    assert last_exc is not None
    raise last_exc


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


def _check_canceled(job_id: str | None) -> None:
    """If the job row was flipped to CANCELED while a previous stage was
    running, abort before starting the next. No-op when job_id is None
    (callers outside Celery — e.g. tests)."""
    if not job_id:
        return
    current = (
        LLMJob.objects
        .filter(id=job_id)
        .values_list("status", flat=True)
        .first()
    )
    if current == LLMJobStatus.CANCELED:
        raise CanceledMidJob(job_id)


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
    pv_id, system_prompt = get_active_prompt("router")
    user_prompt = build_router_user_prompt(user_message, has_scene)
    history_messages = _build_history_messages(history)

    ctx = _CallContext(
        user_id=user_id, username=username, mode="chat-router",
        attempt=1, temperature=0.3, model=flash_model,
        prompt_version_id=pv_id,
    )
    messages = (
        [{"role": "system", "content": system_prompt}]
        + history_messages
        + [{"role": "user", "content": user_prompt}]
    )
    try:
        content = _do_call(ctx, messages, effective_flash_max_tokens())
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
    job_id: str | None,
) -> tuple[dict | None, str]:
    """Step 2: pro model generates scene + reply. One semantic retry on
    parse/validation errors (with a stricter prompt). Transient upstream
    errors are retried automatically by _do_call before reaching here."""
    # Cancel-check before the heavy call — router may have taken seconds and
    # the user could have hit cancel in the meantime.
    _check_canceled(job_id)

    pv_id, system_prompt = get_active_prompt("chat")
    user_prompt = build_chat_user_prompt(user_message, current_scene)
    history_messages = _build_history_messages(history)
    max_tokens = effective_pro_max_tokens()

    ctx_first = _CallContext(
        user_id=user_id, username=username, mode="chat",
        attempt=1, temperature=0.7, model=pro_model,
        prompt_version_id=pv_id,
    )
    messages_first = (
        [{"role": "system", "content": system_prompt}]
        + history_messages
        + [{"role": "user", "content": user_prompt}]
    )

    try:
        content = _do_call(ctx_first, messages_first, max_tokens)
        return _parse_and_validate_chat(content, ctx_first)
    except LLMUpstreamError:
        # Semantic retry — stricter prompt + lower temperature. _do_call has
        # already exhausted its transient-retry budget for the first attempt.
        retry_user_prompt = (
            user_prompt
            + '\n\nSTRICT JSON ONLY. Output: {"scene": ... | null, "message": "<≤200 chars, same language as userMessage>"}.'
            " Field 'message' is REQUIRED and must not be empty."
        )
        ctx_second = _CallContext(
            user_id=user_id, username=username, mode="chat",
            attempt=ctx_first.attempt + 1, temperature=0.4, model=pro_model,
            prompt_version_id=pv_id,
        )
        messages_second = (
            [{"role": "system", "content": system_prompt}]
            + history_messages
            + [{"role": "user", "content": retry_user_prompt}]
        )
        try:
            content = _do_call(ctx_second, messages_second, max_tokens)
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
    job_id: str | None = None,
) -> tuple[dict | None, str]:
    """Public sync entry called by the Celery task. Returns (scene_or_none, message).

    `scene` is null when AI replied without changing the canvas. `message` is
    always present. `flash_model` / `pro_model` are catalog ids (e.g.
    "deepseek-v4-pro", "opencode-go/kimi-k2.6"); fall back to admin defaults
    when not supplied. `job_id` enables cancel-check between router and pro
    stages (no-op when None — used by tests).
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
        job_id=job_id,
    )


# Back-compat alias so older imports keep working through the rename.
call_deepseek = call_llm
