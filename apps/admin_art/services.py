"""Aggregate queries for the admin art dashboard.

Mirrors internal/service/admin_art.go — the joins use COLLATE
utf8mb4_unicode_ci because the production users.id was created with the 0900
collation (incompatible with the studio tables' unicode_ci)."""

from __future__ import annotations

from django.db import connection

from apps.core.tz import local_today_str

LIST_ART_USERS_SQL = """
    SELECT u.id,
           u.username,
           COALESCE(u.display_name, '')        AS display_name,
           COALESCE(today.cnt, 0)              AS used_today,
           COALESCE(total.cnt, 0)              AS used_total,
           COALESCE(art.cnt, 0)                AS artworks
    FROM users u
    LEFT JOIN (
        SELECT user_id, count AS cnt FROM llm_usage WHERE date = %s
    ) today ON today.user_id COLLATE utf8mb4_unicode_ci = u.id COLLATE utf8mb4_unicode_ci
    LEFT JOIN (
        SELECT user_id, SUM(count) AS cnt FROM llm_usage GROUP BY user_id
    ) total ON total.user_id COLLATE utf8mb4_unicode_ci = u.id COLLATE utf8mb4_unicode_ci
    LEFT JOIN (
        SELECT user_id, COUNT(*) AS cnt FROM artworks GROUP BY user_id
    ) art ON art.user_id COLLATE utf8mb4_unicode_ci = u.id COLLATE utf8mb4_unicode_ci
    WHERE today.user_id IS NOT NULL
       OR total.user_id IS NOT NULL
       OR art.user_id   IS NOT NULL
    ORDER BY used_today DESC, used_total DESC, u.username ASC
    LIMIT 500
"""


def list_art_users(limit: int) -> tuple[list[dict], str]:
    today = local_today_str()
    with connection.cursor() as cur:
        cur.execute(LIST_ART_USERS_SQL, [today])
        rows = cur.fetchall()
    out = []
    for user_id, username, display_name, used_today, used_total, artworks in rows:
        remaining = max(limit - used_today, 0)
        out.append(
            {
                "userId": user_id,
                "username": username,
                "displayName": display_name or "",
                "usedToday": used_today,
                "remaining": remaining,
                "usedTotal": used_total,
                "artworks": artworks,
            }
        )
    return out, today


def set_user_quota_today(user_id: str, count: int) -> int:
    if count < 0:
        count = 0
    today = local_today_str()
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO llm_usage (user_id, date, count) VALUES (%s, %s, %s) "
            "ON DUPLICATE KEY UPDATE count = VALUES(count)",
            [user_id, today, count],
        )
    return count


def adjust_user_quota_today(user_id: str, delta: int) -> int:
    today = local_today_str()
    with connection.cursor() as cur:
        cur.execute(
            "SELECT count FROM llm_usage WHERE user_id = %s AND date = %s",
            [user_id, today],
        )
        row = cur.fetchone()
    current = row[0] if row else 0
    return set_user_quota_today(user_id, max(current + delta, 0))


def reset_user_quota_today(user_id: str) -> None:
    today = local_today_str()
    with connection.cursor() as cur:
        cur.execute(
            "DELETE FROM llm_usage WHERE user_id = %s AND date = %s",
            [user_id, today],
        )


def art_stats() -> tuple[int, int, int, int]:
    today = local_today_str()
    with connection.cursor() as cur:
        cur.execute(
            "SELECT COALESCE(SUM(count), 0), COUNT(*) FROM llm_usage WHERE date = %s",
            [today],
        )
        total_today, users_today_hit = cur.fetchone() or (0, 0)
        cur.execute(
            "SELECT COALESCE(SUM(count), 0), COUNT(DISTINCT user_id) FROM llm_usage "
            "WHERE date >= DATE_SUB(%s, INTERVAL 6 DAY)",
            [today],
        )
        total_7d, users_7d = cur.fetchone() or (0, 0)
    return int(total_today), int(users_today_hit), int(total_7d), int(users_7d)
