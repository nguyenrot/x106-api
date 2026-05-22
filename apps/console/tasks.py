"""Celery tasks for the VPS console — now backed by the agy CLI.

agy runs autonomously: it reads files, runs shell commands, and produces a
single text response per turn. No tool-call surfacing, no human approval,
no agent-step budget — agy's own settings.json (toolPermission:always-proceed)
handles its internal tool gating.

Tasks:
- `run_console_chat(message_id)`: one shot — assemble history, call agy,
  store reply, done.
- `cleanup_old_messages`: beat task to keep `console_messages` from growing.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from celery import shared_task
from django.utils import timezone

from apps.console.models import ConsoleMessage, ConsoleSession
from apps.console.services import agy as agy_service
from apps.console.services.settings import get_setting
from apps.console.settings_keys import SETTING_SYSTEM_PROMPT

if TYPE_CHECKING:
    from apps.console.models import ConsoleSession as _Session

logger = logging.getLogger("x106.console.tasks")

# Cap history fed to agy on each turn so the prompt stays manageable.
_MAX_HISTORY_MESSAGES = 12
_MAX_ASSISTANT_CHARS_PER_TURN = 4000


@shared_task(name="apps.console.tasks.run_console_chat", time_limit=300, soft_time_limit=270)
def run_console_chat(message_id: str) -> None:
    """Drive one user→assistant turn end-to-end via agy."""
    try:
        msg = ConsoleMessage.objects.select_related("session").get(pk=message_id)
    except ConsoleMessage.DoesNotExist:
        logger.warning("run_console_chat: message %s gone", message_id)
        return

    if msg.role != ConsoleMessage.ROLE_ASSISTANT:
        logger.error("run_console_chat: expected assistant role on %s, got %r", message_id, msg.role)
        return
    if msg.status not in (ConsoleMessage.STATUS_PENDING, ConsoleMessage.STATUS_STREAMING):
        logger.info("run_console_chat: %s already in terminal status %r — skip", message_id, msg.status)
        return

    updated = ConsoleMessage.objects.filter(
        pk=msg.pk, status__in=[ConsoleMessage.STATUS_PENDING, ConsoleMessage.STATUS_STREAMING]
    ).update(status=ConsoleMessage.STATUS_STREAMING)
    if not updated:
        return

    prompt = _build_prompt(msg.session)

    try:
        result = agy_service.run_agy(prompt)
    except agy_service.AgyError as err:
        _mark_failed(msg, str(err))
        return
    except Exception as err:  # safety net
        logger.exception("run_console_chat: unexpected agy error")
        _mark_failed(msg, f"agy error: {err!r}")
        return

    ConsoleMessage.objects.filter(pk=msg.pk, status=ConsoleMessage.STATUS_STREAMING).update(
        status=ConsoleMessage.STATUS_DONE,
        content=result.text,
    )
    ConsoleSession.objects.filter(pk=msg.session_id).update(updated_at=timezone.now())


def _mark_failed(msg: ConsoleMessage, reason: str) -> None:
    ConsoleMessage.objects.filter(
        pk=msg.pk,
        status__in=[ConsoleMessage.STATUS_PENDING, ConsoleMessage.STATUS_STREAMING],
    ).update(status=ConsoleMessage.STATUS_FAILED, error_message=reason[:2000])


def _build_prompt(session: "_Session") -> str:
    """Assemble the single-string prompt agy --print accepts.

    Layout:
      <system_prompt>

      ## Lịch sử hội thoại (gần nhất ở cuối)
      User: ...
      Trợ lý: ...
      User: ...
      Trợ lý: ...

      ## Yêu cầu mới
      User: <latest user message>

    The most recent user message is the one we want agy to act on. We still
    list it inside the history block too so the layout reads naturally;
    duplicating doesn't confuse agy and saves a special-case branch here.
    """
    system_prompt = get_setting(SETTING_SYSTEM_PROMPT)
    msgs = list(
        session.messages.order_by("-created_at")[:_MAX_HISTORY_MESSAGES]
    )
    msgs.reverse()

    lines: list[str] = [system_prompt.rstrip(), "", "## Lịch sử hội thoại (gần nhất ở cuối)"]
    latest_user: str | None = None
    for m in msgs:
        if m.role == ConsoleMessage.ROLE_USER:
            lines.append(f"User: {m.content}")
            latest_user = m.content
        elif m.role == ConsoleMessage.ROLE_ASSISTANT and m.content:
            snippet = m.content[:_MAX_ASSISTANT_CHARS_PER_TURN]
            lines.append(f"Trợ lý: {snippet}")
        # role=system is configured globally; skip.

    if latest_user is not None:
        lines += ["", "## Yêu cầu mới", f"User: {latest_user}"]
    return "\n".join(lines)


@shared_task(name="apps.console.tasks.cleanup_old_messages")
def cleanup_old_messages() -> None:
    """Hourly: drop assistant messages in terminal status older than 60 days.
    User messages are preserved (they're cheap and form the user's history).
    """
    cutoff = timezone.now() - timedelta(days=60)
    ConsoleMessage.objects.filter(
        role=ConsoleMessage.ROLE_ASSISTANT,
        status__in=[
            ConsoleMessage.STATUS_DONE,
            ConsoleMessage.STATUS_FAILED,
            ConsoleMessage.STATUS_CANCELED,
        ],
        created_at__lt=cutoff,
    ).delete()
