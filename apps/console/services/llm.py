"""Gemini client — manual tool calling via the official `google-genai` SDK.

The agent loop in `apps.console.tasks` is split across multiple Celery tasks
(LLM → return → user approve → SSH exec → resume LLM) which can span minutes
of wall-clock. We therefore use Gemini's **stateless** generate_content call,
passing full history each time and surfacing `function_call` parts back to
the caller as `ToolCall` dataclasses instead of letting the SDK auto-execute.

Public surface (kept identical to the old OpenCode Zen client so `tasks.py`
needs only minor changes): `chat_completion`, `ToolCall`, `CompletionResult`,
`SHELL_TOOL_SCHEMA`, `LLMConfigError`, `LLMTransportError`.

`messages` follow the OpenAI format (system/user/assistant/tool with optional
`tool_calls` / `tool_call_id`); we translate them into Gemini `Content` parts
internally. This avoids touching `_build_history` in tasks.py.

Async core + sync wrapper (`asyncio.run`) — Gemini SDK has both `models` and
`aio.models` clients; we use the async one so each Celery task only spends
the wall-clock it needs on the wire.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

logger = logging.getLogger("x106.console.llm")

# Wall-clock cap for a single chat_completion. The tasks.py soft_time_limit
# is 90s — leave headroom for retries + the SSH layer.
_REQUEST_TIMEOUT_SEC = 60.0


class LLMConfigError(RuntimeError):
    """GEMINI_API_KEY missing or model not allowlisted."""


class LLMTransportError(RuntimeError):
    """SDK call failed (network / 5xx / quota)."""


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
    usage: dict[str, int] = field(default_factory=dict)
    # Kept for backward compat with tasks.py; Gemini doesn't require echoing
    # thoughts back, so this is always empty.
    reasoning_content: str = ""


# OpenAI-style schema kept so tasks.py keeps importing it as a single source
# of truth; converted to Gemini's `FunctionDeclaration` inside chat_completion.
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


def chat_completion(
    messages: list[dict[str, Any]],
    model: str,
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1024,
) -> CompletionResult:
    """Sync façade — runs the async call in a fresh event loop per Celery task.
    Cheap because each task only makes one (or a handful of) LLM calls."""
    return asyncio.run(
        _chat_completion_async(messages, model, tools, temperature, max_tokens)
    )


async def _chat_completion_async(
    messages: list[dict[str, Any]],
    model: str,
    tools: list[dict[str, Any]] | None,
    temperature: float,
    max_tokens: int,
) -> CompletionResult:
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        raise LLMConfigError(
            "GEMINI_API_KEY is not set; cannot call Gemini API"
        )

    system_text, contents = _translate_messages(messages)
    gemini_tools = _translate_tools(tools)

    config = genai_types.GenerateContentConfig(
        system_instruction=system_text or None,
        temperature=temperature,
        max_output_tokens=max_tokens,
        tools=gemini_tools,
        # Disable the SDK's auto-execution: every function_call must bubble up
        # so the agent loop in tasks.py can persist it as a ConsoleExec row
        # awaiting user approval.
        automatic_function_calling=(
            genai_types.AutomaticFunctionCallingConfig(disable=True)
            if gemini_tools
            else None
        ),
    )

    client = genai.Client(api_key=api_key)

    try:
        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            ),
            timeout=_REQUEST_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError as err:
        raise LLMTransportError(f"gemini timeout after {_REQUEST_TIMEOUT_SEC}s") from err
    except genai_errors.APIError as err:
        # Covers 4xx + 5xx from Gemini including quota/rate-limit errors.
        raise LLMTransportError(f"gemini api error: {err}") from err
    except Exception as err:
        raise LLMTransportError(f"gemini call failed: {err!r}") from err

    return _parse_response(response)


def _translate_messages(
    messages: list[dict[str, Any]],
) -> tuple[str, list[genai_types.Content]]:
    """OpenAI-format → (system_instruction string, list of Gemini Content).

    OpenAI                          Gemini
    ──────                          ──────
    system                          (collected into system_instruction)
    user                            Content(role="user", parts=[text])
    assistant (text only)           Content(role="model", parts=[text])
    assistant (with tool_calls)     Content(role="model", parts=[function_call,...])
    tool (tool result)              Content(role="user", parts=[function_response])
    """
    system_parts: list[str] = []
    contents: list[genai_types.Content] = []

    for m in messages:
        role = m.get("role")
        if role == "system":
            text = m.get("content") or ""
            if text:
                system_parts.append(text)
            continue

        if role == "user":
            text = m.get("content") or ""
            contents.append(
                genai_types.Content(role="user", parts=[genai_types.Part(text=text)])
            )
            continue

        if role == "assistant":
            parts: list[genai_types.Part] = []
            text = m.get("content") or ""
            if text:
                parts.append(genai_types.Part(text=text))
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                raw_args = fn.get("arguments")
                args: dict[str, Any] = {}
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args) if raw_args else {}
                    except json.JSONDecodeError:
                        args = {"_raw": raw_args}
                elif isinstance(raw_args, dict):
                    args = raw_args
                parts.append(
                    genai_types.Part(
                        function_call=genai_types.FunctionCall(
                            id=tc.get("id") or "",
                            name=fn.get("name") or "",
                            args=args,
                        )
                    )
                )
            if parts:
                contents.append(genai_types.Content(role="model", parts=parts))
            continue

        if role == "tool":
            # OpenAI's `tool` message holds a function_response keyed by
            # tool_call_id. Gemini wraps function responses as `user` Content.
            raw = m.get("content") or ""
            try:
                payload = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                payload = {"output": raw}
            contents.append(
                genai_types.Content(
                    role="user",
                    parts=[
                        genai_types.Part(
                            function_response=genai_types.FunctionResponse(
                                id=m.get("tool_call_id") or "",
                                name=m.get("name") or "run_shell",
                                response=payload if isinstance(payload, dict) else {"output": payload},
                            )
                        )
                    ],
                )
            )
            continue

    return "\n\n".join(system_parts), contents


def _translate_tools(tools: list[dict[str, Any]] | None) -> list[genai_types.Tool] | None:
    if not tools:
        return None
    declarations: list[genai_types.FunctionDeclaration] = []
    for t in tools:
        if (t.get("type") or "function") != "function":
            continue
        fn = t.get("function") or {}
        declarations.append(
            genai_types.FunctionDeclaration(
                name=fn.get("name") or "",
                description=fn.get("description") or "",
                parameters=_strip_unsupported_schema(fn.get("parameters") or {}),
            )
        )
    if not declarations:
        return None
    return [genai_types.Tool(function_declarations=declarations)]


def _strip_unsupported_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Gemini's FunctionDeclaration accepts a subset of JSON Schema. Our
    SHELL_TOOL_SCHEMA already uses only the supported keys, so this is a
    passthrough — but kept as a chokepoint for future schema additions."""
    return schema


def _parse_response(response: Any) -> CompletionResult:
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []

    candidates = getattr(response, "candidates", None) or []
    finish_reason = ""
    if candidates:
        first = candidates[0]
        finish_reason = str(getattr(first, "finish_reason", "") or "")
        content = getattr(first, "content", None)
        for part in (getattr(content, "parts", None) or []) if content else []:
            fc = getattr(part, "function_call", None)
            if fc is not None and getattr(fc, "name", None):
                args = getattr(fc, "args", None) or {}
                # Gemini may return MapComposite / dict-like — coerce to plain dict.
                if not isinstance(args, dict):
                    try:
                        args = dict(args)
                    except (TypeError, ValueError):
                        args = {"_raw": str(args)}
                tool_calls.append(
                    ToolCall(
                        id=getattr(fc, "id", "") or "",
                        name=fc.name,
                        arguments=args,
                    )
                )
                continue
            t = getattr(part, "text", None)
            if t:
                text_parts.append(t)

    usage: dict[str, int] = {}
    um = getattr(response, "usage_metadata", None)
    if um is not None:
        for k in ("prompt_token_count", "candidates_token_count", "total_token_count"):
            v = getattr(um, k, None)
            if isinstance(v, int):
                usage[k] = v

    return CompletionResult(
        text="".join(text_parts).strip(),
        tool_calls=tool_calls,
        raw_finish_reason=finish_reason,
        usage=usage,
    )
