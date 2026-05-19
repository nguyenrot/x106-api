"""Streak math + mood histogram for /journal/vibes/stats.

Count consecutive days back from today (Asia/Ho_Chi_Minh) where the user
either logged a vibe OR applied a streak freeze; stop at the first true gap.
"""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

from .models import StreakFreeze, Vibe
from apps.core.tz import local_today


def compute_stats(user_id: str) -> dict:
    rows = list(Vibe.objects.filter(user_id=user_id).values("date", "mood_emoji"))
    total = len(rows)
    if total == 0:
        return {"total_entries": 0, "streak": 0, "mood_counts": {}}

    date_set = {r["date"] for r in rows}
    frozen_set = set(
        StreakFreeze.objects.filter(user_id=user_id).values_list("applied_date", flat=True)
    )
    cursor = local_today()
    streak = 0
    while cursor in date_set or cursor in frozen_set:
        streak += 1
        cursor -= timedelta(days=1)

    mood_counts = dict(Counter(r["mood_emoji"] for r in rows))
    return {"total_entries": total, "streak": streak, "mood_counts": mood_counts}
