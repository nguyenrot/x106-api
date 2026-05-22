"""Celery tasks for the VPS console.

Two foreground tasks (`run_console_chat`, `run_console_exec`) drive the agent
loop; two beat tasks (`recover_stuck_execs`, `cleanup_old_execs`) keep the DB
sane.

Race-safety: every status transition is a single conditional UPDATE so a
cancel from the API can interrupt a worker mid-step without losing rows.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.console.models import ConsoleExec, ConsoleMessage, ConsoleSession
from apps.console.services import llm as llm_service
from apps.console.services import ssh as ssh_service
from apps.console.services.danger import classify
from apps.console.services.settings import get_int, get_setting
from apps.console.settings_keys import (
    SETTING_AI_MODEL,
    SETTING_COMMAND_TIMEOUT_SEC,
    SETTING_MAX_AGENT_STEPS,
    SETTING_SYSTEM_PROMPT,
)

logger = logging.getLogger("x106.console.tasks")

# Hard caps that protect us regardless of settings:
_MAX_HISTORY_MESSAGES = 20
_MAX_OUTPUT_BYTES_TO_LLM = 8000  # truncate stdout/stderr fed back to the model


# ─── run_console_chat ─────────────────────────────────────────────────────


@shared_task(name="apps.console.tasks.run_console_chat", time_limit=120, soft_time_limit=90)
def run_console_chat(message_id: str) -> None:
    """One step of the agent loop. Re-enqueued by `run_console_exec` after each
    tool result, so the chain is: chat → tool propose → confirm → exec → chat
    → ... → final assistant text."""
    try:
        msg = ConsoleMessage.objects.select_related("session").get(pk=message_id)
    except ConsoleMessage.DoesNotExist:
        logger.warning("run_console_chat: message %s gone", message_id)
        return

    if msg.role != ConsoleMessage.ROLE_ASSISTANT:
        logger.error("run_console_chat: expected assistant role on %s, got %r", message_id, msg.role)
        return
    if msg.status not in (ConsoleMessage.STATUS_PENDING, ConsoleMessage.STATUS_STREAMING):
        logger.info("run_console_chat: %s already in terminal status %r — skipping", message_id, msg.status)
        return

    max_steps = get_int(SETTING_MAX_AGENT_STEPS)
    if msg.step_count >= max_steps:
        _mark_message_failed(msg, f"agent loop limit ({max_steps} steps)")
        return

    # Atomic pending → streaming transition.
    updated = ConsoleMessage.objects.filter(
        pk=msg.pk, status__in=[ConsoleMessage.STATUS_PENDING, ConsoleMessage.STATUS_STREAMING]
    ).update(status=ConsoleMessage.STATUS_STREAMING)
    if not updated:
        logger.info("run_console_chat: %s status changed mid-flight — skipping", message_id)
        return

    history = _build_history(msg.session)
    model = get_setting(SETTING_AI_MODEL)

    try:
        result = llm_service.chat_completion(
            messages=history,
            model=model,
            tools=[llm_service.SHELL_TOOL_SCHEMA],
        )
    except llm_service.LLMConfigError as err:
        _mark_message_failed(msg, f"LLM not configured: {err}")
        return
    except llm_service.LLMTransportError as err:
        _mark_message_failed(msg, f"LLM transport: {err}")
        return
    except Exception as err:  # safety net
        logger.exception("run_console_chat: unexpected LLM error")
        _mark_message_failed(msg, f"LLM error: {err!r}")
        return

    if result.tool_calls:
        # Persist the proposed commands as ConsoleExec rows; the user has to
        # approve each via the API.
        with transaction.atomic():
            for tc in result.tool_calls:
                if tc.name != "run_shell":
                    continue
                command = (tc.arguments.get("command") or "").strip()
                if not command:
                    continue
                level, reasons = classify(command)
                ConsoleExec.objects.create(
                    session=msg.session,
                    message=msg,
                    user=msg.session.user,
                    command=command,
                    source=ConsoleExec.SOURCE_AI_PROPOSED,
                    status=ConsoleExec.STATUS_AWAITING_CONFIRM,
                    danger_level=level,
                    danger_reasons=reasons,
                    tool_call_id=tc.id,
                )
            # Save any prose the model wrote alongside the tool call.
            ConsoleMessage.objects.filter(pk=msg.pk, status=ConsoleMessage.STATUS_STREAMING).update(
                status=ConsoleMessage.STATUS_AWAITING_CONFIRM,
                content=result.text,
            )
        return

    # No tool calls — final assistant turn.
    ConsoleMessage.objects.filter(pk=msg.pk, status=ConsoleMessage.STATUS_STREAMING).update(
        status=ConsoleMessage.STATUS_DONE,
        content=result.text,
    )
    ConsoleSession.objects.filter(pk=msg.session_id).update(updated_at=timezone.now())


def _mark_message_failed(msg: ConsoleMessage, reason: str) -> None:
    ConsoleMessage.objects.filter(
        pk=msg.pk,
        status__in=[ConsoleMessage.STATUS_PENDING, ConsoleMessage.STATUS_STREAMING],
    ).update(status=ConsoleMessage.STATUS_FAILED, error_message=reason[:2000])


def _build_history(session: ConsoleSession) -> list[dict]:
    """Walk the session's messages + execs and assemble the OpenAI-format
    messages array for the next chat completion."""
    system_prompt = get_setting(SETTING_SYSTEM_PROMPT)
    out: list[dict] = [{"role": "system", "content": system_prompt}]

    msgs = list(
        session.messages.order_by("created_at")
        .prefetch_related("execs")[: _MAX_HISTORY_MESSAGES]
    )

    for m in msgs:
        if m.role == ConsoleMessage.ROLE_USER:
            out.append({"role": "user", "content": m.content})
            continue
        if m.role == ConsoleMessage.ROLE_SYSTEM:
            # Skip — we already injected the live system prompt above.
            continue
        # assistant — may have tool_calls + tool results to splice in.
        execs = list(m.execs.order_by("created_at"))
        proposed = [e for e in execs if e.source == ConsoleExec.SOURCE_AI_PROPOSED and e.tool_call_id]

        if proposed:
            assistant_msg: dict = {"role": "assistant", "content": m.content or ""}
            assistant_msg["tool_calls"] = [
                {
                    "id": e.tool_call_id,
                    "type": "function",
                    "function": {
                        "name": "run_shell",
                        "arguments": _json_dumps({"command": e.command}),
                    },
                }
                for e in proposed
            ]
            out.append(assistant_msg)
            for e in proposed:
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": e.tool_call_id,
                        "content": _tool_result_payload(e),
                    }
                )
        else:
            # Plain assistant text message.
            if m.content:
                out.append({"role": "assistant", "content": m.content})
    return out


def _json_dumps(obj: dict) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)


def _tool_result_payload(e: ConsoleExec) -> str:
    if e.status == ConsoleExec.STATUS_DENIED:
        return _json_dumps(
            {"error": "user denied execution", "deny_reason": e.deny_reason or ""}
        )
    if e.status == ConsoleExec.STATUS_CANCELED:
        return _json_dumps({"error": "user canceled execution"})
    if e.status == ConsoleExec.STATUS_FAILED:
        return _json_dumps({"error": e.error_message or "execution failed"})
    if e.status != ConsoleExec.STATUS_DONE:
        return _json_dumps({"error": f"execution not finished (status={e.status})"})
    out = (e.stdout or "")[:_MAX_OUTPUT_BYTES_TO_LLM]
    err = (e.stderr or "")[:_MAX_OUTPUT_BYTES_TO_LLM]
    return _json_dumps(
        {
            "exit_code": e.exit_code,
            "latency_ms": e.latency_ms,
            "stdout": out,
            "stderr": err,
        }
    )


# ─── run_console_exec ─────────────────────────────────────────────────────


@shared_task(name="apps.console.tasks.run_console_exec", time_limit=120, soft_time_limit=90)
def run_console_exec(exec_id: str) -> None:
    try:
        exec_row = ConsoleExec.objects.select_related("message").get(pk=exec_id)
    except ConsoleExec.DoesNotExist:
        logger.warning("run_console_exec: %s gone", exec_id)
        return

    # Atomic approved → running.
    updated = ConsoleExec.objects.filter(
        pk=exec_row.pk, status=ConsoleExec.STATUS_APPROVED
    ).update(status=ConsoleExec.STATUS_RUNNING, started_at=timezone.now())
    if not updated:
        logger.info("run_console_exec: %s no longer approved — skipping", exec_id)
        return

    timeout = get_int(SETTING_COMMAND_TIMEOUT_SEC)

    try:
        result = ssh_service.run_command(exec_row.command, timeout)
    except ssh_service.SSHConfigError as err:
        ConsoleExec.objects.filter(pk=exec_row.pk, status=ConsoleExec.STATUS_RUNNING).update(
            status=ConsoleExec.STATUS_FAILED,
            error_message=f"ssh config: {err}",
            finished_at=timezone.now(),
        )
        _resume_chat_if_linked(exec_row)
        return
    except Exception as err:
        logger.exception("run_console_exec: unexpected error")
        ConsoleExec.objects.filter(pk=exec_row.pk, status=ConsoleExec.STATUS_RUNNING).update(
            status=ConsoleExec.STATUS_FAILED,
            error_message=f"ssh error: {err!r}",
            finished_at=timezone.now(),
        )
        _resume_chat_if_linked(exec_row)
        return

    new_status = (
        ConsoleExec.STATUS_FAILED if result.timed_out else ConsoleExec.STATUS_DONE
    )
    err_msg = "timeout exceeded" if result.timed_out else ""

    ConsoleExec.objects.filter(pk=exec_row.pk, status=ConsoleExec.STATUS_RUNNING).update(
        status=new_status,
        stdout=result.stdout[:200000],
        stderr=result.stderr[:200000],
        exit_code=result.exit_code,
        latency_ms=result.latency_ms,
        error_message=err_msg,
        finished_at=timezone.now(),
    )
    _resume_chat_if_linked(exec_row)


def _resume_chat_if_linked(exec_row: ConsoleExec) -> None:
    """If this exec was proposed by the AI as part of an agent loop, bump the
    parent message's step_count and re-enqueue run_console_chat. Direct
    user-typed execs also link to their user message (so the UI can group
    them) but must NOT trigger a chat resume."""
    if not exec_row.message_id:
        return
    if exec_row.source != ConsoleExec.SOURCE_AI_PROPOSED:
        return
    # Only re-enqueue once *all* sibling execs (the model could have proposed
    # multiple tool calls in one turn) have left awaiting_confirm/approved/running.
    pending_siblings = ConsoleExec.objects.filter(
        message_id=exec_row.message_id,
        status__in=[
            ConsoleExec.STATUS_AWAITING_CONFIRM,
            ConsoleExec.STATUS_APPROVED,
            ConsoleExec.STATUS_RUNNING,
        ],
    ).exists()
    if pending_siblings:
        return
    ConsoleMessage.objects.filter(pk=exec_row.message_id).update(
        step_count=F("step_count") + 1,
        status=ConsoleMessage.STATUS_STREAMING,
    )
    run_console_chat.delay(exec_row.message_id)


# ─── Beat tasks ───────────────────────────────────────────────────────────


@shared_task(name="apps.console.tasks.recover_stuck_execs")
def recover_stuck_execs() -> None:
    """Mark execs that have been `running` for too long (worker likely died)
    as failed. Same for messages that have been `streaming` for >5min."""
    timeout = get_int(SETTING_COMMAND_TIMEOUT_SEC)
    cutoff_exec = timezone.now() - timedelta(seconds=max(120, timeout * 3))
    ConsoleExec.objects.filter(
        status=ConsoleExec.STATUS_RUNNING, started_at__lt=cutoff_exec
    ).update(
        status=ConsoleExec.STATUS_FAILED,
        error_message="task lost (worker died or hung)",
        finished_at=timezone.now(),
    )

    cutoff_msg = timezone.now() - timedelta(minutes=5)
    ConsoleMessage.objects.filter(
        status__in=[ConsoleMessage.STATUS_PENDING, ConsoleMessage.STATUS_STREAMING],
        created_at__lt=cutoff_msg,
    ).update(status=ConsoleMessage.STATUS_FAILED, error_message="task lost")


@shared_task(name="apps.console.tasks.cleanup_old_execs")
def cleanup_old_execs() -> None:
    """Hourly: drop execs in terminal status older than 30 days."""
    cutoff = timezone.now() - timedelta(days=30)
    ConsoleExec.objects.filter(
        status__in=[
            ConsoleExec.STATUS_DONE,
            ConsoleExec.STATUS_FAILED,
            ConsoleExec.STATUS_CANCELED,
            ConsoleExec.STATUS_DENIED,
        ],
        finished_at__lt=cutoff,
    ).delete()
