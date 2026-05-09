"""Celery beat tasks: recover orphaned jobs + delete old terminal rows.

Mirrors RecoverStaleJobs / CleanupOldLLMJobs from internal/service/llm_jobs.go
and the worker.go schedule (60s recovery, 30min cleanup).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from . import quota
from .models import LLMJob, LLMJobStatus

log = logging.getLogger("x106.studio.maintenance")

STALE_JOB_AGE = timedelta(seconds=720)
CLEANUP_MAX_AGE = timedelta(hours=24)


@shared_task(name="apps.studio.maintenance.recover_stale_jobs")
def recover_stale_jobs() -> int:
    cutoff = timezone.now() - STALE_JOB_AGE
    stale = list(
        LLMJob.objects.filter(status=LLMJobStatus.PROCESSING, started_at__lt=cutoff).values_list(
            "id", "user_id"
        )
    )
    if not stale:
        return 0
    count = 0
    for job_id, user_id in stale:
        with transaction.atomic():
            updated = LLMJob.objects.filter(id=job_id, status=LLMJobStatus.PROCESSING).update(
                status=LLMJobStatus.FAILED,
                error_message="worker timeout / crash recovery",
                finished_at=timezone.now(),
            )
            if updated:
                quota.refund(user_id)
                count += 1
    if count:
        log.info("recover_stale_jobs: recovered %d", count)
    return count


@shared_task(name="apps.studio.maintenance.cleanup_old_jobs")
def cleanup_old_jobs() -> int:
    cutoff = timezone.now() - CLEANUP_MAX_AGE
    deleted, _ = (
        LLMJob.objects.filter(
            status__in=[LLMJobStatus.DONE, LLMJobStatus.FAILED, LLMJobStatus.CANCELED],
            finished_at__lt=cutoff,
        )
        .delete()
    )
    if deleted:
        log.info("cleanup_old_jobs: deleted %d", deleted)
    return deleted
