"""Daily LLM quota — atomic upserts on llm_usage(user_id, date).

`reserve()` uses a conditional UPDATE-then-INSERT pattern that's race-safe
without explicit locks: a second concurrent caller cannot drive count past
the limit because the UPDATE filters on `count < limit` atomically.
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
    """Unconditional +1. Deprecated for new code — prefer reserve() which
    enforces the limit atomically. Kept for legacy callers."""
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
    """Atomically charge a quota slot. Raises QuotaExceeded if at the cap.

    Strategy (race-safe without row locks):
    1. Conditional UPDATE `count = count + 1 WHERE count < limit`. If the row
       exists and isn't at limit, MySQL atomically bumps it and returns 1.
    2. If rowcount == 0, the row may not exist OR be at limit. Try
       INSERT IGNORE with count=1 — succeeds only if no row exists yet.
    3. If INSERT IGNORE didn't insert (rowcount == 0), a concurrent caller
       just inserted before us; retry the conditional UPDATE once. The retry
       catches the race where step 1 raced against the first INSERT.
    4. Still 0 → user is at the cap. Raise QuotaExceeded.
    """
    if limit <= 0:
        # Admin disabled or misconfigured — no slot available, don't even
        # create a row. Saves polluting llm_usage with phantom count=1 rows.
        raise QuotaExceeded()

    today = local_today_str()
    with connection.cursor() as cur:
        # 1. Try conditional UPDATE first.
        cur.execute(
            "UPDATE llm_usage SET count = count + 1 "
            "WHERE user_id = %s AND date = %s AND count < %s",
            [user_id, today, limit],
        )
        if cur.rowcount == 1:
            return _read_back_count(cur, user_id, today, limit)

        # 2. UPDATE didn't match — either no row yet OR at cap. Try INSERT.
        cur.execute(
            "INSERT IGNORE INTO llm_usage (user_id, date, count) VALUES (%s, %s, 1)",
            [user_id, today],
        )
        if cur.rowcount == 1:
            # Fresh row created with count=1.
            return 1, max(limit - 1, 0)

        # 3. Concurrent INSERT beat us. Retry conditional UPDATE once.
        cur.execute(
            "UPDATE llm_usage SET count = count + 1 "
            "WHERE user_id = %s AND date = %s AND count < %s",
            [user_id, today, limit],
        )
        if cur.rowcount == 1:
            return _read_back_count(cur, user_id, today, limit)

        # 4. Row exists AND at limit.
        raise QuotaExceeded()


def _read_back_count(cur, user_id: str, today: str, limit: int) -> tuple[int, int]:
    cur.execute(
        "SELECT count FROM llm_usage WHERE user_id = %s AND date = %s",
        [user_id, today],
    )
    new_count = cur.fetchone()[0]
    return new_count, max(limit - new_count, 0)
