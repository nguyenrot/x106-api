"""OpenCode Zen client — OpenAI-compatible chat completions with tool use.

Only one endpoint matters: `POST {base}/chat/completions`. We hit it with
httpx, parse the response choice, and return a normalized dict the task layer
can hand back as either an assistant message or a tool-call request.

Free models (allowlisted in `apps.console.settings_keys.ALLOWED_MODELS`):
- deepseek-v4-flash-free (default, best tool calling)
- big-pickle
- qwen-3.6-plus-free
- minimax-m2.5-free

Retries: 3 attempts with exponential backoff for 429/5xx/network errors;
non-retryable 4xx is raised immediately. 60s overall wall-clock per attempt
(idle watchdog handled by httpx read timeout).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
from django.conf import settings

logger = logging.getLogger("x106.console.llm")


class LLMConfigError(RuntimeError):
    """OPENCODE_ZEN_API_KEY missing or model not allowlisted."""


class LLMTransportError(RuntimeError):
    """All retries exhausted on a network / 5xx error."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class CompletionResult:
    text: str
    tool_calls: list[ToolCall]
    raw_finish_reason: str
    usage: dict[str, int]
    # DeepSeek "thinking" mode payload. Empty for non-reasoning models. When
    # non-empty, MUST be echoed back on the assistant message in the next
    # chat completion turn or DeepSeek 400s.
    reasoning_content: str = ""


def _headers() -> dict[str, str]:
    key = settings.OPENCODE_ZEN_API_KEY
    if not key:
        raise LLMConfigError(
            "OPENCODE_ZEN_API_KEY is not set; cannot call OpenCode Zen"
        )
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def chat_completion(
    messages: list[dict[str, Any]],
    model: str,
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1024,
) -> CompletionResult:
    """One-shot chat completion. `messages` follow the OpenAI format
    (`role`, `content`, optional `tool_calls` / `tool_call_id` / `name`).

    Returns a `CompletionResult` with either text content, tool calls, or
    both. The caller decides what to do based on `finish_reason`."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    url = f"{settings.OPENCODE_ZEN_BASE_URL.rstrip('/')}/chat/completions"
    timeout = httpx.Timeout(connect=10.0, read=60.0, write=15.0, pool=10.0)

    last_err: Exception | None = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(url, headers=_headers(), json=payload)
            if resp.status_code in {429, 500, 502, 503, 504}:
                raise LLMTransportError(
                    f"transient {resp.status_code}: {resp.text[:200]}"
                )
            resp.raise_for_status()
            return _parse(resp.json())
        except (httpx.TransportError, LLMTransportError) as err:
            last_err = err
            wait = 2 ** attempt
            logger.warning(
                "chat_completion attempt %d failed: %r; retry in %ds",
                attempt + 1,
                err,
                wait,
            )
            time.sleep(wait)
        except httpx.HTTPStatusError as err:
            # 4xx other than 429 — not retryable.
            raise LLMTransportError(
                f"http {err.response.status_code}: {err.response.text[:200]}"
            ) from err

    raise LLMTransportError(f"exhausted retries; last={last_err!r}")


def _parse(body: dict[str, Any]) -> CompletionResult:
    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    text = (message.get("content") or "").strip()
    raw_tool_calls = message.get("tool_calls") or []
    tool_calls: list[ToolCall] = []
    for tc in raw_tool_calls:
        if (tc.get("type") or "function") != "function":
            continue
        fn = tc.get("function") or {}
        raw_args = fn.get("arguments")
        args: dict[str, Any] = {}
        if isinstance(raw_args, str):
            import json

            try:
                args = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError:
                args = {"_raw": raw_args}
        elif isinstance(raw_args, dict):
            args = raw_args
        tool_calls.append(
            ToolCall(
                id=tc.get("id") or "",
                name=fn.get("name") or "",
                arguments=args,
            )
        )

    return CompletionResult(
        text=text,
        tool_calls=tool_calls,
        raw_finish_reason=str(choice.get("finish_reason") or ""),
        usage=body.get("usage") or {},
        reasoning_content=str(message.get("reasoning_content") or ""),
    )


# Tool schema for `run_shell` — passed to chat_completion's `tools` arg.
SHELL_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "run_shell",
        "description": (
            "Chạy một lệnh shell trên VPS qua SSH. MỌI lệnh đều cần user "
            "approve thủ công — không được giả định lệnh đã chạy thành công. "
            "Output sẽ được trả lại trong tool result message."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Lệnh shell duy nhất, một dòng.",
                },
            },
            "required": ["command"],
        },
    },
}
