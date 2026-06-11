"""Celery entrypoints for the cafe auto-review agent.

Lives at the app root (not in ./agent/) because celery's autodiscover_tasks
only imports `apps.<app>.tasks` — a nested module would silently never
register.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from .agent.runner import run_cafe_agent
from .models import CafeAgentRun

logger = logging.getLogger("apps.cafe.agent")


# Worst case: 2 agy attempts (300s each) + agy cover vetting (240s) + geocode
# → needs more than the global 620s celery cap.
@shared_task(name="apps.cafe.tasks.generate_cafe_review", time_limit=1060, soft_time_limit=1000)
def generate_cafe_review(slot: str = "daily") -> str:
    """Research + publish one cafe review for the daily beat slot.

    Returns a status string; benign outcomes (disabled/skip) never raise so
    beat doesn't mark the periodic task as errored.
    """
    # Beat can double-fire on worker/clock restarts — one success per slot per
    # 20h window is plenty for a 1-post-per-day cadence.
    recent = timezone.now() - timedelta(hours=20)
    if CafeAgentRun.objects.filter(
        slot=slot, status="succeeded", started_at__gte=recent
    ).exists():
        logger.info("generate_cafe_review: %s already succeeded recently; skipping", slot)
        return f"skipped:already-ran-{slot}"

    result = run_cafe_agent(slot)
    if result.review is not None:
        return f"{result.status}:{result.review.slug}"
    return f"{result.status}:{result.reason}"


@shared_task(name="apps.cafe.tasks.run_cafe_agent_now", time_limit=1060, soft_time_limit=1000)
def run_cafe_agent_now(run_id: str) -> str:
    """Execute a manually-requested run (admin "Tạo bài AI" button).

    The CafeAgentRun row is created by the endpoint so the client gets its id
    immediately; this task does the slow work on that same row. force=True so
    it works even while the scheduled agent is disabled.
    """
    try:
        run = CafeAgentRun.objects.get(pk=run_id)
    except CafeAgentRun.DoesNotExist:
        logger.warning("run_cafe_agent_now: CafeAgentRun %s not found", run_id)
        return "missing"
    result = run_cafe_agent("manual", force=True, run=run)
    if result.review is not None:
        return f"{result.status}:{result.review.slug}"
    return f"{result.status}:{result.reason}"
