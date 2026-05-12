"""Celery tasks for the async LLM job pipeline.

A submitted job is a row in `llm_jobs` (status=pending) plus a Celery task
queued to Redis. The task picks up the row, runs DeepSeek, writes the result.
The frontend polls `/studio/llm/job/{id}` for the same row — Celery owns the
queue, the row is just status the frontend can read.
"""

from __future__ import annotations

import logging

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.utils import timezone

# Load sibling task modules so Celery's autodiscover (which only imports
# `tasks.py` per INSTALLED_APP) sees the beat-scheduled maintenance tasks.
from . import (
    maintenance,  # noqa: F401
    quota,
)
from .errors import LLMOffError, LLMUpstreamError, SceneValidationError
from .models import LLMJob, LLMJobStatus
from .services.llm import CanceledMidJob, call_llm
from .services.prompts import get_active_prompt

log = logging.getLogger("x106.studio.tasks")


@shared_task(
    name="apps.studio.tasks.run_llm_job",
    bind=True,
    time_limit=620,
    soft_time_limit=600,
    max_retries=0,
)
def run_llm_job(self, job_id: str) -> None:
    job = LLMJob.objects.filter(id=job_id).first()
    if job is None:
        log.warning("run_llm_job: job %s not found", job_id)
        return

    if job.status != LLMJobStatus.PENDING:
        log.info("run_llm_job: job %s already in status=%s, skipping", job_id, job.status)
        return

    # Snapshot the active chat prompt id at job-start so admin can see exactly
    # which prompt version produced each scene. Audit trail survives later
    # prompt edits. Router prompt id is recorded per-attempt in LLMRequestLog.
    chat_prompt_id, _ = get_active_prompt("chat")

    job.status = LLMJobStatus.PROCESSING
    job.started_at = timezone.now()
    job.attempt += 1
    job.prompt_version_id = chat_prompt_id
    job.save(update_fields=["status", "started_at", "attempt", "prompt_version_id"])

    body = job.request_body or {}
    mode = job.mode

    try:
        scene, assistant_message = call_llm(
            user_id=job.user_id,
            username=job.username,
            user_message=body.get("userMessage") or "",
            current_scene=body.get("currentScene"),
            history=body.get("history"),
            flash_model=job.flash_model or None,
            pro_model=job.pro_model or None,
            job_id=job.id,
        )
    except CanceledMidJob:
        # User canceled between router and pro. Worker exits cleanly — the
        # cancel_job view already set status=CANCELED and finished_at. Quota
        # refund happened in the view too.
        log.info("run_llm_job: %s canceled mid-job", job_id)
        return
    except SoftTimeLimitExceeded:
        log.error("run_llm_job: %s soft-time-limit", job_id)
        _terminal_update(job_id, LLMJobStatus.FAILED, "worker time limit exceeded")
        quota.refund(job.user_id)
        raise
    except (LLMUpstreamError, LLMOffError, SceneValidationError) as exc:
        log.warning("run_llm_job: %s failed: %s", job_id, exc)
        _terminal_update(job_id, LLMJobStatus.FAILED, str(exc))
        quota.refund(job.user_id)
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("run_llm_job: %s crashed", job_id)
        _terminal_update(job_id, LLMJobStatus.FAILED, f"internal error: {exc}")
        quota.refund(job.user_id)
        return

    # Success — but only commit if the row is still PROCESSING. Conditional
    # UPDATE prevents the worker from overwriting a status=CANCELED row that
    # the cancel_job view just wrote (race with terminate=True revoke).
    updated = (
        LLMJob.objects
        .filter(id=job_id, status=LLMJobStatus.PROCESSING)
        .update(
            status=LLMJobStatus.DONE,
            result_scene=scene,
            result_message=assistant_message,
            error_message=None,
            finished_at=timezone.now(),
        )
    )
    if updated == 0:
        log.info(
            "run_llm_job: %s finished but row already terminal — discarding result",
            job_id,
        )
        return
    shape_count = len((scene or {}).get("shapes", []))
    log.info(
        "run_llm_job: %s done mode=%s shapes=%d msg=%s",
        job_id, mode, shape_count, bool(assistant_message),
    )


def _terminal_update(job_id: str, status: str, error_message: str) -> None:
    """Conditional terminal write — same race guard as the success path."""
    LLMJob.objects.filter(
        id=job_id, status=LLMJobStatus.PROCESSING
    ).update(
        status=status,
        error_message=error_message[:500],
        finished_at=timezone.now(),
    )
