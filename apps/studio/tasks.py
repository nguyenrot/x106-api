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

from . import quota
from .errors import LLMOffError, LLMUpstreamError, SceneValidationError
from .models import LLMJob, LLMJobStatus
from .services.deepseek import call_deepseek

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

    job.status = LLMJobStatus.PROCESSING
    job.started_at = timezone.now()
    job.attempt += 1
    job.save(update_fields=["status", "started_at", "attempt"])

    body = job.request_body or {}
    mode = job.mode

    try:
        scene = call_deepseek(
            user_id=job.user_id,
            username=job.username,
            mode=mode,
            current_scene=body.get("currentScene"),
            stroke_count=int(body.get("strokeCount") or 0),
        )
    except SoftTimeLimitExceeded:
        log.error("run_llm_job: %s soft-time-limit", job_id)
        job.status = LLMJobStatus.FAILED
        job.error_message = "worker time limit exceeded"[:500]
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error_message", "finished_at"])
        quota.refund(job.user_id)
        raise
    except (LLMUpstreamError, LLMOffError, SceneValidationError) as exc:
        log.warning("run_llm_job: %s failed: %s", job_id, exc)
        job.status = LLMJobStatus.FAILED
        job.error_message = str(exc)[:500]
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error_message", "finished_at"])
        quota.refund(job.user_id)
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("run_llm_job: %s crashed", job_id)
        job.status = LLMJobStatus.FAILED
        job.error_message = f"internal error: {exc}"[:500]
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error_message", "finished_at"])
        quota.refund(job.user_id)
        return

    job.status = LLMJobStatus.DONE
    job.result_scene = scene
    job.error_message = None
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "result_scene", "error_message", "finished_at"])
    log.info("run_llm_job: %s done shapes=%d", job_id, len(scene.get("shapes", [])))
