"""Daily LLM quota — atomic upserts on llm_usage(user_id, date).

Mirrors GetQuota/incrementUsage/ReserveLLMQuota/RefundLLMQuota in
internal/service/llm.go. Uses raw SQL so the INSERT...ON DUPLICATE KEY UPDATE
semantics match exactly (Django's get_or_create has a different race profile).
"""

from __future__ import annotations

from django.db import connection

from apps.core.tz import local_today_str

from .errors import QuotaExceeded


def get_quota(user_id: str, limit: int) -> tuple[int, int]:
    """Return (used, remaining) for today."""
    today = local_today_str()
    with connection.cursor() as cur:
        cur.execute(
            "SELECT count FROM llm_usage WHERE user_id = %s AND date = %s LIMIT 1",
            [user_id, today],
        )
        row = cur.fetchone()
    used = row[0] if row else 0
    remaining = max(limit - used, 0)
    return used, remaining


def increment(user_id: str) -> int:
    today = local_today_str()
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO llm_usage (user_id, date, count) VALUES (%s, %s, 1) "
            "ON DUPLICATE KEY UPDATE count = count + 1",
            [user_id, today],
        )
        cur.execute(
            "SELECT count FROM llm_usage WHERE user_id = %s AND date = %s",
            [user_id, today],
        )
        return cur.fetchone()[0]


def refund(user_id: str) -> None:
    today = local_today_str()
    with connection.cursor() as cur:
        cur.execute(
            "UPDATE llm_usage SET count = GREATEST(count - 1, 0) "
            "WHERE user_id = %s AND date = %s",
            [user_id, today],
        )


def reserve(user_id: str, limit: int) -> tuple[int, int]:
    """Atomically charge a quota slot. Raises QuotaExceeded if at the cap."""
    _, remaining = get_quota(user_id, limit)
    if remaining <= 0:
        raise QuotaExceeded()
    new_count = increment(user_id)
    new_remaining = max(limit - new_count, 0)
    return new_count, new_remaining
