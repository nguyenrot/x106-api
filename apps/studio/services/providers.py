"""Provider adapters that turn a model id + messages into a raw string response.

Three concrete providers:
  - DeepSeekProvider          DeepSeek native (OpenAI-compatible, streaming)
  - OpenCodeOpenAIProvider    OpenCode Go OpenAI-compat endpoint, streaming
  - OpenCodeAnthropicProvider OpenCode Go Anthropic-compat endpoint (/v1/messages)

The Anthropic adapter has to translate request/response shape — it can't share
the OpenAI client. We use raw httpx (already a dep) instead of pulling in the
anthropic SDK; the surface we touch is small (system + messages + max_tokens
+ content[].text) and stable.

Reasoning-style models on OpenCode (most of them) emit a `reasoning` field
separate from `content`. We strip the reasoning entirely — the caller only
needs the final JSON. For the OpenAI-compat path that means relying on
`message.content` (which is what the SDK exposes); the OpenAI SDK ignores
the `reasoning` field, so we just get the model's final answer.

Every provider raises domain exceptions (LLMUpstreamError / LLMTimeoutError)
on failure so the caller can record a structured log row.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

import httpx
from django.conf import settings
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from ..errors import LLMTimeoutError, LLMUpstreamError
from .model_catalog import ModelSpec, get_model

log = logging.getLogger("x106.studio.providers")

LOG_TRUNCATE = 600


@dataclass
class ProviderResult:
    """Raw text + optional token usage stats from a single provider call.

    Token counts default to 0 when the upstream omits usage info."""
    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    finish_reason: str = ""
    chunk_count: int = 0


# ─── DeepSeek native ─────────────────────────────────────────────────────────

_deepseek_client: OpenAI | None = None


def _get_deepseek_client() -> OpenAI:
    global _deepseek_client
    if _deepseek_client is None:
        _deepseek_client = OpenAI(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL,
            timeout=httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0),
            max_retries=0,
        )
    return _deepseek_client


# ─── OpenCode Go (OpenAI-compatible) ─────────────────────────────────────────

_opencode_openai_client: OpenAI | None = None


def _get_opencode_openai_client() -> OpenAI:
    global _opencode_openai_client
    if _opencode_openai_client is None:
        _opencode_openai_client = OpenAI(
            api_key=settings.OPENCODE_API_KEY,
            base_url=settings.OPENCODE_BASE_URL,
            timeout=httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0),
            max_retries=0,
        )
    return _opencode_openai_client


def _call_openai_compat(
    client: OpenAI,
    *,
    remote_model: str,
    messages: list[dict],
    max_tokens: int,
    temperature: float,
    response_format_json: bool,
) -> ProviderResult:
    """Shared streaming call for any OpenAI-compat backend (DeepSeek + OpenCode)."""
    params: dict = {
        "model": remote_model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if response_format_json:
        params["response_format"] = {"type": "json_object"}

    parts: list[str] = []
    finish_reason = ""
    chunk_count = 0
    prompt_tokens = completion_tokens = total_tokens = 0

    try:
        stream = client.chat.completions.create(**params)
        for chunk in stream:
            chunk_count += 1
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
                completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
                total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
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
        raise LLMUpstreamError(
            f"upstream status={exc.status_code}: {body_text[:200]}"
        ) from exc
    except APITimeoutError as exc:
        raise LLMTimeoutError(str(exc)) from exc
    except APIConnectionError as exc:
        raise LLMUpstreamError(f"connection error: {exc}") from exc

    return ProviderResult(
        content="".join(parts),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        finish_reason=finish_reason,
        chunk_count=chunk_count,
    )


# ─── OpenCode Go (Anthropic-compatible /v1/messages) ─────────────────────────

_opencode_anthropic_client: httpx.Client | None = None


def _get_opencode_anthropic_client() -> httpx.Client:
    """Thin httpx client; we hand-build the Anthropic request body ourselves."""
    global _opencode_anthropic_client
    if _opencode_anthropic_client is None:
        _opencode_anthropic_client = httpx.Client(
            base_url=settings.OPENCODE_ANTHROPIC_BASE_URL,
            timeout=httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0),
            headers={
                "x-api-key": settings.OPENCODE_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
    return _opencode_anthropic_client


def _split_system_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    """Anthropic puts the system prompt at top level; pull every "system" role
    out of the list (concat in order) and return alongside the non-system messages."""
    system_parts: list[str] = []
    rest: list[dict] = []
    for m in messages:
        if m.get("role") == "system":
            content = m.get("content") or ""
            if isinstance(content, str) and content:
                system_parts.append(content)
        else:
            rest.append(m)
    return ("\n\n".join(system_parts), rest)


def _call_anthropic_compat(
    *,
    remote_model: str,
    messages: list[dict],
    max_tokens: int,
    temperature: float,
) -> ProviderResult:
    system, chat_messages = _split_system_messages(messages)
    body: dict = {
        "model": remote_model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": chat_messages,
    }
    if system:
        body["system"] = system

    client = _get_opencode_anthropic_client()
    try:
        resp = client.post("/messages", json=body)
    except httpx.TimeoutException as exc:
        raise LLMTimeoutError(str(exc)) from exc
    except httpx.HTTPError as exc:
        raise LLMUpstreamError(f"connection error: {exc}") from exc

    if resp.status_code >= 400:
        raise LLMUpstreamError(
            f"upstream status={resp.status_code}: {resp.text[:200]}"
        )

    try:
        payload = resp.json()
    except ValueError as exc:
        raise LLMUpstreamError(f"invalid json from upstream: {exc}") from exc

    if payload.get("type") == "error":
        err = payload.get("error", {})
        raise LLMUpstreamError(
            f"upstream error: {err.get('type', 'unknown')}: {err.get('message', '')[:200]}"
        )

    # Anthropic shape: content is an array of blocks, each {type: "text"|"thinking", ...}.
    # We only want text blocks (thinking is the reasoning trace; the model puts the
    # JSON answer in a text block).
    content_parts: list[str] = []
    for block in payload.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text") or ""
            if isinstance(text, str):
                content_parts.append(text)

    usage = payload.get("usage") or {}
    return ProviderResult(
        content="".join(content_parts),
        prompt_tokens=int(usage.get("input_tokens", 0) or 0),
        completion_tokens=int(usage.get("output_tokens", 0) or 0),
        total_tokens=int(usage.get("input_tokens", 0) or 0) + int(usage.get("output_tokens", 0) or 0),
        finish_reason=payload.get("stop_reason") or "",
        chunk_count=1,
    )


# ─── Dispatcher ──────────────────────────────────────────────────────────────


class ProviderNotConfigured(LLMUpstreamError):
    """Raised when we'd need a key (OPENCODE_API_KEY) that hasn't been set."""


def call_provider(
    *,
    model_id: str,
    messages: list[dict],
    max_tokens: int,
    temperature: float,
    response_format_json: bool = True,
) -> ProviderResult:
    """Single entry point — picks the right provider for the catalog model id."""
    spec: ModelSpec | None = get_model(model_id)
    if spec is None:
        raise LLMUpstreamError(f"unknown model: {model_id}")

    if spec.provider == "deepseek":
        if not settings.DEEPSEEK_API_KEY:
            raise ProviderNotConfigured("DeepSeek API key not configured")
        return _call_openai_compat(
            _get_deepseek_client(),
            remote_model=spec.remote_id,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format_json=response_format_json,
        )

    if spec.provider == "opencode_openai":
        if not settings.OPENCODE_API_KEY:
            raise ProviderNotConfigured("OpenCode API key not configured")
        # Some OpenCode reasoning models reject response_format=json_object;
        # we still ask for it on OpenAI-compat and fall back to plain prompting
        # if the upstream returns a 400. Tolerant retry sits at the caller.
        return _call_openai_compat(
            _get_opencode_openai_client(),
            remote_model=spec.remote_id,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format_json=response_format_json,
        )

    if spec.provider == "opencode_anthropic":
        if not settings.OPENCODE_API_KEY:
            raise ProviderNotConfigured("OpenCode API key not configured")
        return _call_anthropic_compat(
            remote_model=spec.remote_id,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    raise LLMUpstreamError(f"no provider for {spec.provider}")
