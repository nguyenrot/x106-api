"""DeepSeek streaming client + scene-call orchestration.

Two pipelines coexist:

- **Hierarchical** (random mode): a v4-pro best-of-2 race produces a small
  outline (palette + layout + clusters), then v4-flash expands every cluster
  in parallel into shapes. Final scene = merged shapes + outline header.
  Wall time ~60–100s (vs 200–400s monolithic), per-call output stays under
  ~4500 tokens so we never trip the soft-time-limit.

- **Monolithic** (polish/remix): the original single-call pattern with a
  retry on parse/upstream failures. These modes operate on a bounded existing
  scene and don't need the sharded approach.

httpx `read=180s` catches network idle at the socket level; OpenAI APITimeoutError
maps to LLMTimeoutError. `max_retries=0` on the SDK so our orchestration owns
all retry policy.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random as stdlib_random
import time
from dataclasses import dataclass, field
from decimal import Decimal

import httpx
from django.conf import settings
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, OpenAI

from ..errors import (
    ClusterExpansionError,
    LLMDisabledError,
    LLMOffError,
    LLMTimeoutError,
    LLMUpstreamError,
    OutlineValidationError,
    SceneValidationError,
)
from ..models import LLMRequestLog
from ..settings_keys import effective_model, llm_enabled
from .prompts import (
    CHAT_SYSTEM_PROMPT,
    EXPAND_SYSTEM_PROMPT,
    OUTLINE_SYSTEM_PROMPT,
    build_chat_user_prompt,
    build_expand_user_prompt,
    build_outline_user_prompt,
    build_system_prompt,
    build_user_prompt,
)
from .scene import (
    SCENE_VERSION,
    validate_and_clamp_scene,
    validate_chat_response,
    validate_outline,
)

log = logging.getLogger("x106.studio.deepseek")

# Per-call output ceilings (kept well under the 600s Celery soft-time-limit
# even at the slowest observed ~30 tok/s of v4-pro).
MAX_TOKENS = 6000           # monolithic (polish/remix)
OUTLINE_MAX_TOKENS = 1800   # outline JSON is small (~500 tokens of content)
EXPAND_MAX_TOKENS = 4500    # one cluster ≤ 40 shapes ≈ 3.5k tokens of content
LOG_TRUNCATE = 600

OUTLINE_RACE_PARALLEL = 2          # number of v4-pro outline calls launched
OUTLINE_PER_CALL_TIMEOUT = 110.0   # asyncio guard; httpx read=180s is the harder ceiling
EXPAND_PER_CALL_TIMEOUT = 90.0
EXPAND_PER_CLUSTER_RETRIES = 1     # 1 retry → at most 2 attempts before fallback

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


def _make_async_client() -> AsyncOpenAI:
    """Async client is NOT cached: Celery's sync task calls asyncio.run() per
    job, which creates a fresh event loop each time. A cached AsyncOpenAI's
    underlying httpx.AsyncClient would still be bound to the previous (closed)
    loop. We instantiate per `_call_random_async` and use it as a context
    manager so the connection pool releases cleanly when the loop tears down."""
    return AsyncOpenAI(
        api_key=settings.DEEPSEEK_API_KEY,
        base_url=settings.DEEPSEEK_BASE_URL,
        timeout=httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0),
        max_retries=0,
    )


@dataclass
class _CallContext:
    user_id: str
    username: str
    mode: str            # "random:outline", "random:expand", "polish", "remix"
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


# ─── Sync streaming call (monolithic pipeline) ────────────────────────────

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


def _parse_and_validate_scene(content: str, ctx: _CallContext) -> dict:
    """Parse JSON + validate as LLMScene v3. Mutates ctx for logging."""
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


def _call_monolithic_sync(
    user_id: str, username: str, mode: str, current_scene: dict | None, stroke_count: int
) -> dict:
    """Single-call DeepSeek with one retry on upstream/parse errors. Used for polish/remix."""
    model = effective_model()
    system_prompt = build_system_prompt()
    user_prompt = build_user_prompt(mode, current_scene, stroke_count)

    ctx_first = _CallContext(
        user_id=user_id, username=username, mode=mode,
        attempt=1, temperature=0.9, model=model,
    )

    messages_first = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    try:
        content = _do_call_sync(ctx_first, messages_first, MAX_TOKENS)
        return _parse_and_validate_scene(content, ctx_first)
    except LLMUpstreamError:
        retry_user_prompt = (
            user_prompt
            + "\n\nSTRICT JSON ONLY. Output đúng schema LLMScene v3. "
            "Mọi shape phải có đầy đủ: id, shape, color, material, motion, position[3], size[3], scale. Tối thiểu 4 shapes."
        )
        ctx_second = _CallContext(
            user_id=user_id, username=username, mode=mode,
            attempt=2, temperature=0.5, model=model,
        )
        messages_second = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": retry_user_prompt},
        ]
        try:
            content = _do_call_sync(ctx_second, messages_second, MAX_TOKENS)
            return _parse_and_validate_scene(content, ctx_second)
        finally:
            _record_log(ctx_second)
    finally:
        _record_log(ctx_first)


# ─── Chat (single v4-pro call with multi-turn history) ───────────────────

CHAT_MAX_TOKENS = 4000  # chat scenes are smaller; cap output to keep latency tight


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
    # Log scene if present; otherwise the message-only payload for debugging.
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


# ─── Async streaming call (hierarchical pipeline) ─────────────────────────

async def _do_call_async(client: AsyncOpenAI, ctx: _CallContext, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
    """Async variant of _do_call_sync. Same semantics, async iteration on stream."""
    params = {
        "model": ctx.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
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
        stream = await client.chat.completions.create(**params)
        async for chunk in stream:
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


# ─── Stage 1 — outline (race best-of-2 v4-pro) ────────────────────────────

async def _outline_one_attempt(client: AsyncOpenAI, ctx: _CallContext, user_prompt: str) -> dict:
    """One outline attempt. Returns validated outline dict; mutates ctx for logging."""
    try:
        content = await asyncio.wait_for(
            _do_call_async(client, ctx, OUTLINE_SYSTEM_PROMPT, user_prompt, OUTLINE_MAX_TOKENS),
            timeout=OUTLINE_PER_CALL_TIMEOUT,
        )
    except asyncio.TimeoutError:
        ctx.status = "asyncio_timeout"
        ctx.error_message = f"outline call exceeded {OUTLINE_PER_CALL_TIMEOUT}s"
        raise LLMTimeoutError(ctx.error_message)

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        ctx.status = "invalid_outline_json"
        ctx.error_message = str(exc)
        raise LLMUpstreamError(f"invalid outline json: {exc}") from exc

    try:
        outline = validate_outline(parsed)
    except OutlineValidationError as exc:
        ctx.status = "outline_validation_error"
        ctx.error_message = str(exc)
        raise

    ctx.status = "success"
    ctx.parsed_scene = outline  # logged into the parsed_direction column for debugging
    return outline


async def _outline_stage(client: AsyncOpenAI, user_id: str, username: str, stroke_count: int) -> dict:
    """Race OUTLINE_RACE_PARALLEL v4-pro calls; first valid outline wins."""
    model = effective_model()
    user_prompt = build_outline_user_prompt(stroke_count)

    contexts: list[_CallContext] = [
        _CallContext(
            user_id=user_id, username=username, mode="random:outline",
            attempt=i + 1,
            temperature=0.85 + (i * 0.15),  # 0.85, 1.00 — diversify slightly
            model=model,
        )
        for i in range(OUTLINE_RACE_PARALLEL)
    ]
    tasks: list[asyncio.Task] = [
        asyncio.create_task(_outline_one_attempt(client, ctx, user_prompt))
        for ctx in contexts
    ]

    winner: dict | None = None
    pending: set[asyncio.Task] = set(tasks)

    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                if t.exception() is None:
                    winner = t.result()
                    break
                # else: ctx already recorded the error; try the next finisher
            if winner is not None:
                break

        if winner is None:
            raise LLMUpstreamError("all outline attempts failed")
        return winner
    finally:
        for t in pending:
            t.cancel()
        if pending:
            # Drain cancellations so httpx connections release cleanly.
            await asyncio.gather(*pending, return_exceptions=True)
        for ctx in contexts:
            _record_log(ctx)


# ─── Stage 2 — expand (parallel v4-flash, one call per cluster) ──────────

def _coerce_cluster_shapes(raw_shapes: list, cluster: dict, expected: int) -> list[dict]:
    """Normalize LLM's raw shape array: lock shape/material/motion to the cluster's
    spec, fix IDs to `<cluster_id>_<i>`, truncate to `expected`. Position/size
    pass through to validate_and_clamp_scene which handles bbox/range clamping."""
    out: list[dict] = []
    cid = cluster["id"]
    for i, raw in enumerate(raw_shapes[:expected]):
        if not isinstance(raw, dict):
            raw = {}
        shape: dict = {
            "id": f"{cid}_{i}",
            "shape": cluster["shapeKind"],
            "color": raw.get("color") or cluster["colorAnchor"],
            "material": cluster["material"],
            "motion": cluster["motion"],
            "position": raw.get("position") or [0.0, 0.0, 0.0],
            "size": raw.get("size") or [1.0, 1.0, 1.0],
            "scale": raw.get("scale") or cluster["scaleRange"][0],
        }
        if "rotation" in raw:
            shape["rotation"] = raw["rotation"]
        out.append(shape)

    if len(out) < expected:
        padded = _fallback_cluster_shapes(cluster)
        out.extend(padded[len(out):expected])
    return out


def _fallback_cluster_shapes(cluster: dict) -> list[dict]:
    """Synthesize N shapes for a cluster when LLM call fails or shorts.
    Deterministic per cluster.id; golden-angle distribution around region center."""
    n = cluster["count"]
    region = cluster["region"]
    centers = {
        "top": (0.0, 1.0, 0.0),
        "bottom": (0.0, -1.0, 0.0),
        "left": (-1.4, 0.0, 0.0),
        "right": (1.4, 0.0, 0.0),
        "center": (0.0, 0.0, 0.0),
        "scattered": (0.0, 0.0, 0.0),
    }
    spreads = {
        "top": (1.8, 0.4, 0.4),
        "bottom": (1.8, 0.4, 0.4),
        "left": (0.7, 1.2, 0.4),
        "right": (0.7, 1.2, 0.4),
        "center": (0.6, 0.5, 0.3),
        "scattered": (1.8, 1.2, 0.5),
    }
    cx, cy, cz = centers.get(region, (0.0, 0.0, 0.0))
    sx, sy, sz = spreads.get(region, (1.8, 1.2, 0.5))
    rng = stdlib_random.Random(cluster["id"])
    lo, hi = cluster["scaleRange"]

    shapes: list[dict] = []
    for i in range(n):
        theta = i * 2.39996  # golden angle in radians (~137.5°)
        r = 0.18 * math.sqrt(max(i, 0))
        x = cx + (r * math.cos(theta)) * sx
        y = cy + (r * math.sin(theta)) * sy
        z = cz + rng.uniform(-0.3, 0.3) * sz
        scale = lo + (hi - lo) * (i / max(n - 1, 1))
        shapes.append({
            "id": f"{cluster['id']}_{i}",
            "shape": cluster["shapeKind"],
            "color": cluster["colorAnchor"],
            "material": cluster["material"],
            "motion": cluster["motion"],
            "position": [
                round(max(-2.5, min(2.5, x)), 2),
                round(max(-1.6, min(1.6, y)), 2),
                round(max(-1.0, min(1.0, z)), 2),
            ],
            "size": [1.0, 1.0, 1.0],
            "scale": round(scale, 2),
        })
    return shapes


async def _expand_one_cluster_attempt(client: AsyncOpenAI, ctx: _CallContext, outline: dict, cluster: dict) -> list[dict]:
    user_prompt = build_expand_user_prompt(outline, cluster)
    try:
        content = await asyncio.wait_for(
            _do_call_async(client, ctx, EXPAND_SYSTEM_PROMPT, user_prompt, EXPAND_MAX_TOKENS),
            timeout=EXPAND_PER_CALL_TIMEOUT,
        )
    except asyncio.TimeoutError:
        ctx.status = "asyncio_timeout"
        ctx.error_message = f"expand call exceeded {EXPAND_PER_CALL_TIMEOUT}s"
        raise LLMTimeoutError(ctx.error_message)

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        ctx.status = "invalid_expand_json"
        ctx.error_message = str(exc)
        raise LLMUpstreamError(f"invalid expand json: {exc}") from exc

    raw_shapes = parsed.get("shapes") if isinstance(parsed, dict) else None
    if not isinstance(raw_shapes, list) or not raw_shapes:
        ctx.status = "expand_empty_shapes"
        ctx.error_message = "no shapes in expand response"
        raise ClusterExpansionError("no shapes")

    shapes = _coerce_cluster_shapes(raw_shapes, cluster, cluster["count"])
    ctx.parsed_scene = {"shapes": shapes}
    ctx.status = "success"
    return shapes


async def _expand_one_cluster(client: AsyncOpenAI, user_id: str, username: str, outline: dict, cluster: dict) -> list[dict]:
    """Expand a single cluster with up to EXPAND_PER_CLUSTER_RETRIES retries.
    On total failure, returns deterministic Python-generated fallback shapes
    so the pipeline never hard-fails because of one bad cluster."""
    flash_model = settings.DEEPSEEK_FLASH_MODEL or "deepseek-v4-flash"
    last_error: Exception | None = None

    for attempt in range(EXPAND_PER_CLUSTER_RETRIES + 1):
        ctx = _CallContext(
            user_id=user_id, username=username, mode="random:expand",
            attempt=attempt + 1,
            temperature=0.7 + attempt * 0.1,
            model=flash_model,
        )
        try:
            return await _expand_one_cluster_attempt(client, ctx, outline, cluster)
        except (LLMUpstreamError, LLMTimeoutError, ClusterExpansionError) as exc:
            last_error = exc
            log.warning("expand cluster %s attempt %d failed: %s", cluster["id"], attempt + 1, exc)
        finally:
            _record_log(ctx)

    log.warning(
        "expand cluster %s falling back to placeholder shapes after %d attempts (last_error=%s)",
        cluster["id"], EXPAND_PER_CLUSTER_RETRIES + 1, last_error,
    )
    return _fallback_cluster_shapes(cluster)


async def _expand_stage(client: AsyncOpenAI, user_id: str, username: str, outline: dict) -> list[dict]:
    """Run one _expand_one_cluster per cluster in parallel; concat results."""
    coros = [
        _expand_one_cluster(client, user_id, username, outline, cluster)
        for cluster in outline["clusters"]
    ]
    results = await asyncio.gather(*coros)
    flat: list[dict] = []
    for shapes in results:
        flat.extend(shapes)
    return flat


# ─── Stage 3 — merge ──────────────────────────────────────────────────────

def _build_scene_from_outline(outline: dict, shapes: list[dict]) -> dict:
    """Assemble final LLMScene v3 from outline header + expanded shapes."""
    scene: dict = {
        "version": SCENE_VERSION,
        "title": outline["title"],
        "paletteId": outline["paletteId"],
        "shapes": shapes,
        "texts": outline.get("texts") or [],
    }
    if outline.get("background"):
        scene["background"] = outline["background"]
    if outline.get("aiNotes"):
        scene["aiNotes"] = outline["aiNotes"]
    return scene


# ─── Top-level coroutine for hierarchical mode ────────────────────────────

async def _call_random_async(user_id: str, username: str, stroke_count: int) -> dict:
    async with _make_async_client() as client:
        outline = await _outline_stage(client, user_id, username, stroke_count)
        shapes = await _expand_stage(client, user_id, username, outline)
        raw_scene = _build_scene_from_outline(outline, shapes)
        return validate_and_clamp_scene(raw_scene)


# ─── Public sync entry (called by Celery task) ────────────────────────────

def call_deepseek(
    user_id: str,
    username: str,
    mode: str,
    current_scene: dict | None,
    stroke_count: int,
    user_message: str | None = None,
    history: list[dict] | None = None,
) -> tuple[dict | None, str | None]:
    """Dispatch to the right pipeline. Returns (scene_or_none, assistant_message_or_none).

    - random/polish/remix → (scene, None)
    - chat → (scene_or_none, message); message is always present, scene is null
      when AI replied without changing the canvas.
    """
    if not settings.DEEPSEEK_API_KEY:
        raise LLMDisabledError()
    if not llm_enabled():
        raise LLMOffError()

    if mode == "chat":
        if not user_message:
            raise LLMUpstreamError("chat mode requires user_message")
        return _call_chat_sync(user_id, username, user_message, history, current_scene)

    if mode == "random":
        scene = asyncio.run(_call_random_async(user_id, username, stroke_count))
        return (scene, None)

    scene = _call_monolithic_sync(user_id, username, mode, current_scene, stroke_count)
    return (scene, None)
