"""Aggregation helpers for /ledger/transactions/summary."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Iterable

from .models import LedgerAccount, LedgerKind, LedgerTransaction


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def totals_for(rows: Iterable[LedgerTransaction]) -> dict:
    rows = list(rows)
    income = sum(r.amount for r in rows if r.kind == LedgerKind.INCOME)
    expense = sum(r.amount for r in rows if r.kind == LedgerKind.EXPENSE)
    return {
        "income": income,
        "expense": expense,
        "net": income - expense,
        "count": len(rows),
    }


def compute_summary(
    account: LedgerAccount,
    date_from: str,
    date_to: str,
    group_by: str,
) -> dict:
    df = _parse_date(date_from)
    dt = _parse_date(date_to)
    if dt < df:
        df, dt = dt, df

    rows = list(
        LedgerTransaction.objects.filter(
            account=account,
            occurred_on__gte=df,
            occurred_on__lte=dt,
        ).values("kind", "amount", "category", "occurred_on")
    )

    income_total = sum(r["amount"] for r in rows if r["kind"] == LedgerKind.INCOME)
    expense_total = sum(r["amount"] for r in rows if r["kind"] == LedgerKind.EXPENSE)

    bucket_fmt = "%Y-%m" if group_by == "month" else "%Y-%m-%d"
    bucket_income: dict[str, int] = defaultdict(int)
    bucket_expense: dict[str, int] = defaultdict(int)
    for r in rows:
        key = r["occurred_on"].strftime(bucket_fmt)
        if r["kind"] == LedgerKind.INCOME:
            bucket_income[key] += r["amount"]
        else:
            bucket_expense[key] += r["amount"]
    bucket_keys = sorted(set(bucket_income) | set(bucket_expense))
    buckets = [
        {
            "bucket": k,
            "income": bucket_income[k],
            "expense": bucket_expense[k],
            "net": bucket_income[k] - bucket_expense[k],
        }
        for k in bucket_keys
    ]

    category_income: dict[str, int] = defaultdict(int)
    category_expense: dict[str, int] = defaultdict(int)
    for r in rows:
        bucket = category_income if r["kind"] == LedgerKind.INCOME else category_expense
        bucket[r["category"]] += r["amount"]

    return {
        "from": df.strftime("%Y-%m-%d"),
        "to": dt.strftime("%Y-%m-%d"),
        "group_by": group_by,
        "totals": {
            "income": income_total,
            "expense": expense_total,
            "net": income_total - expense_total,
            "count": len(rows),
        },
        "buckets": buckets,
        "by_category": {
            "income": [
                {"category": k, "amount": v}
                for k, v in sorted(category_income.items(), key=lambda kv: -kv[1])
            ],
            "expense": [
                {"category": k, "amount": v}
                for k, v in sorted(category_expense.items(), key=lambda kv: -kv[1])
            ],
        },
    }
