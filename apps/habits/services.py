"""Stats for habits: frequency-aware streaks, completion rates, and a daily
completion heatmap.

Streaks respect the habit's schedule:
- daily        — consecutive days completed (today still in progress doesn't break it).
- weekly_days  — consecutive *scheduled* weekdays completed (non-scheduled days skipped).
- weekly_count — consecutive ISO weeks where completed count >= weekly_target.

All "today" math uses Asia/Ho_Chi_Minh via apps.core.tz.local_today.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

from .models import Frequency, Habit, HabitLog

WINDOW_DAYS = 140  # ~20 weeks — used for the heatmap and rolling rates
_GUARD = 15000     # hard cap on day-walk loops (never hit in practice)


# ── scheduling helpers ─────────────────────────────────────────────────────

def is_due_today(habit: Habit, today: date) -> bool:
    """Should this habit appear on the Today list?"""
    if habit.frequency == Frequency.WEEKLY_DAYS:
        return today.weekday() in set(habit.weekdays or [])
    return True  # daily + weekly_count surface every day


def _scheduled_fn(habit: Habit):
    """A predicate `(date) -> bool` for daily / weekly_days habits."""
    if habit.frequency == Frequency.WEEKLY_DAYS:
        days = set(habit.weekdays or [])
        return lambda d: d.weekday() in days
    return lambda _d: True


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())  # Monday


# ── per-habit stats ────────────────────────────────────────────────────────

def _rate(scheduled, completed: set[date], today: date, days: int) -> float | None:
    start = today - timedelta(days=days - 1)
    expected = done = 0
    d = start
    while d <= today:
        if scheduled(d):
            expected += 1
            if d in completed:
                done += 1
        d += timedelta(days=1)
    return round(done / expected, 3) if expected else None


def _calendar_stats(habit: Habit, completed: set[date], today: date) -> dict:
    scheduled = _scheduled_fn(habit)
    if habit.frequency == Frequency.WEEKLY_DAYS and not (habit.weekdays or []):
        return {"current_streak": 0, "longest_streak": 0, "completion_rate": None,
                "total_completions": len(completed), "week": None}

    # current streak — don't penalise a still-open today
    cur = 0
    d = today
    if scheduled(d) and d not in completed:
        d -= timedelta(days=1)
    guard = 0
    while guard < _GUARD:
        guard += 1
        if not scheduled(d):
            d -= timedelta(days=1)
            continue
        if d in completed:
            cur += 1
            d -= timedelta(days=1)
        else:
            break

    # longest streak — scan scheduled days from first completion to today
    longest = 0
    if completed:
        run = 0
        d = min(completed)
        while d <= today and guard < _GUARD:
            guard += 1
            if scheduled(d):
                if d in completed:
                    run += 1
                    longest = max(longest, run)
                else:
                    run = 0
            d += timedelta(days=1)
    longest = max(longest, cur)

    return {"current_streak": cur, "longest_streak": longest,
            "completion_rate": _rate(scheduled, completed, today, 30),
            "total_completions": len(completed), "week": None}


def _weekly_count_stats(habit: Habit, completed: set[date], today: date) -> dict:
    target = habit.weekly_target or 1
    weekly: Counter = Counter()
    for d in completed:
        weekly[d.isocalendar()[:2]] += 1

    def wk(d: date):
        return d.isocalendar()[:2]

    # current streak in weeks (current partial week doesn't break it)
    cur = 0
    cursor = today
    if weekly[wk(today)] < target:
        cursor = today - timedelta(days=7)
    guard = 0
    while guard < 1000:
        guard += 1
        if weekly[wk(cursor)] >= target:
            cur += 1
            cursor -= timedelta(days=7)
        else:
            break

    # longest run of satisfied weeks
    longest = 0
    if completed:
        run = 0
        seen: set = set()
        d = min(completed)
        while d <= today and guard < 5000:
            guard += 1
            k = wk(d)
            if k not in seen:
                seen.add(k)
                if weekly[k] >= target:
                    run += 1
                    longest = max(longest, run)
                else:
                    run = 0
            d += timedelta(days=7)
    longest = max(longest, cur)

    # rate over the last 4 completed weeks
    satisfied = sum(1 for i in range(1, 5) if weekly[wk(today - timedelta(days=7 * i))] >= target)

    return {"current_streak": cur, "longest_streak": longest,
            "completion_rate": round(satisfied / 4, 3),
            "total_completions": len(completed),
            "week": {"count": weekly[wk(today)], "target": target}}


def _habit_stats(habit: Habit, completed: set[date], today: date) -> dict:
    if habit.frequency == Frequency.WEEKLY_COUNT:
        return _weekly_count_stats(habit, completed, today)
    return _calendar_stats(habit, completed, today)


# ── heatmap ────────────────────────────────────────────────────────────────

def _heatmap(habits, completed_by_habit, today: date, start: date) -> list[dict]:
    out: list[dict] = []
    d = start
    while d <= today:
        total = done = 0
        for h in habits:
            if h.created_at and h.created_at.date() > d:
                continue
            if h.frequency == Frequency.WEEKLY_DAYS and d.weekday() not in set(h.weekdays or []):
                continue
            total += 1
            if d in completed_by_habit.get(h.id, set()):
                done += 1
        out.append({
            "date": d.isoformat(),
            "total": total,
            "completed": done,
            "ratio": round(done / total, 3) if total else 0,
        })
        d += timedelta(days=1)
    return out


# ── entry point ────────────────────────────────────────────────────────────

def compute_stats(account) -> dict:
    from apps.core.tz import local_today

    today = local_today()
    start = today - timedelta(days=WINDOW_DAYS - 1)

    habits = list(Habit.objects.filter(account=account, archived=False))
    habit_ids = [h.id for h in habits]

    completed_by_habit: dict[str, set[date]] = defaultdict(set)
    for r in HabitLog.objects.filter(account=account, habit_id__in=habit_ids, completed=True).values(
        "habit_id", "date"
    ):
        completed_by_habit[r["habit_id"]].add(r["date"])

    per_habit = []
    for h in habits:
        completed = completed_by_habit.get(h.id, set())
        per_habit.append({
            "id": h.id, "name": h.name, "icon": h.icon, "color": h.color,
            "type": h.type, "frequency": h.frequency,
            **_habit_stats(h, completed, today),
        })

    active = len(habits)
    rates = [p["completion_rate"] for p in per_habit if p["completion_rate"] is not None]
    overall = {
        "active_habits": active,
        "today_completed": sum(1 for h in habits if today in completed_by_habit.get(h.id, set())),
        "today_total": sum(1 for h in habits if is_due_today(h, today)),
        "best_current_streak": max((p["current_streak"] for p in per_habit), default=0),
        "best_longest_streak": max((p["longest_streak"] for p in per_habit), default=0),
        "completion_rate_30": round(sum(rates) / len(rates), 3) if rates else None,
        "total_completions": sum(p["total_completions"] for p in per_habit),
    }

    return {
        "overall": overall,
        "habits": per_habit,
        "heatmap": _heatmap(habits, completed_by_habit, today, start),
    }
