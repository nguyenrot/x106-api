"""Habit + HabitLog models.

Auth is token-based and SHARED with ledger: one opaque token (a LedgerAccount)
works on both /ledger/* and /habits/*. Habit/HabitLog therefore hang off
`ledger.LedgerAccount` (table `ledger_accounts`) via a cross-app FK with
db_constraint=False (no DB-level constraint across apps; scoped in app code).

A HabitLog is one check-in per (habit, date); for quantitative habits it carries
a `count` and a derived `completed` flag (count >= target).
"""

from __future__ import annotations

from django.db import models

from apps.core.ids import new_id


class HabitType(models.TextChoices):
    BINARY = "binary", "Binary"        # done / not done
    COUNT = "count", "Count"          # count toward a target (e.g. 8 glasses)


class Frequency(models.TextChoices):
    DAILY = "daily", "Every day"
    WEEKLY_DAYS = "weekly_days", "Specific weekdays"   # uses `weekdays`
    WEEKLY_COUNT = "weekly_count", "Times per week"    # uses `weekly_target`


class Habit(models.Model):
    id = models.CharField(primary_key=True, max_length=36, default=new_id, editable=False)
    account = models.ForeignKey(
        "ledger.LedgerAccount",
        on_delete=models.CASCADE,
        db_column="account_id",
        db_constraint=False,
        related_name="habits",
    )
    name = models.CharField(max_length=120)
    icon = models.CharField(max_length=40, blank=True, default="")   # phosphor slug, e.g. "drop"
    color = models.CharField(max_length=20, blank=True, default="")  # palette key, e.g. "green"

    type = models.CharField(max_length=10, choices=HabitType.choices, default=HabitType.BINARY)
    target_count = models.PositiveIntegerField(null=True, blank=True)  # required when type=count
    unit = models.CharField(max_length=24, blank=True, default="")     # "ly", "phút", "trang"

    frequency = models.CharField(max_length=16, choices=Frequency.choices, default=Frequency.DAILY)
    weekdays = models.JSONField(default=list, blank=True)              # list[int] 0=Mon..6=Sun
    weekly_target = models.PositiveSmallIntegerField(null=True, blank=True)  # for weekly_count

    category = models.CharField(max_length=40, blank=True, default="")
    tags = models.JSONField(default=list, blank=True)

    reminder_enabled = models.BooleanField(default=False)
    reminder_time = models.TimeField(null=True, blank=True)           # local HH:MM (Asia/Ho_Chi_Minh)

    sort_order = models.IntegerField(default=0)
    archived = models.BooleanField(default=False)
    archived_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "habits"
        ordering = ["sort_order", "created_at"]
        indexes = [
            models.Index(fields=["account", "archived"], name="idx_habits_acct_archived"),
        ]


class HabitLog(models.Model):
    id = models.CharField(primary_key=True, max_length=36, default=new_id, editable=False)
    habit = models.ForeignKey(
        Habit,
        on_delete=models.CASCADE,
        db_column="habit_id",
        db_constraint=False,
        related_name="logs",
    )
    account = models.ForeignKey(
        "ledger.LedgerAccount",
        on_delete=models.CASCADE,
        db_column="account_id",
        db_constraint=False,
        related_name="habit_logs",
    )
    date = models.DateField()
    count = models.PositiveIntegerField(default=1)   # binary always 1; quantitative = progress
    completed = models.BooleanField(default=True)    # binary: count>=1; count: count>=target
    note = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "habit_logs"
        ordering = ["-date"]
        constraints = [
            models.UniqueConstraint(fields=["habit", "date"], name="uq_habit_logs_habit_date"),
        ]
        indexes = [
            models.Index(fields=["account", "date"], name="idx_habit_logs_acct_date"),
        ]
