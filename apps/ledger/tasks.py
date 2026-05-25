"""Celery tasks for ledger."""

from __future__ import annotations

import logging

from celery import shared_task
from django.db import transaction

from apps.core.tz import local_today

from .models import LedgerAccount, LedgerKind, LedgerTransaction

logger = logging.getLogger(__name__)

AUTO_EXPENSE_AMOUNT = 200_000
AUTO_EXPENSE_CATEGORY = "other"
AUTO_EXPENSE_NOTE = "Chi mặc định cuối ngày"


@shared_task(name="apps.ledger.tasks.auto_expense_for_missing_days")
def auto_expense_for_missing_days() -> dict:
    """Mỗi tối 23:50 VN: với mọi LedgerAccount không có record chi nào trong ngày,
    tự động thêm 1 record chi 200.000đ vào category `other`."""
    today = local_today()
    created = 0
    skipped = 0

    for account in LedgerAccount.objects.all().only("id"):
        with transaction.atomic():
            has_expense = LedgerTransaction.objects.filter(
                account=account,
                kind=LedgerKind.EXPENSE,
                occurred_on=today,
            ).exists()
            if has_expense:
                skipped += 1
                continue
            LedgerTransaction.objects.create(
                account=account,
                kind=LedgerKind.EXPENSE,
                amount=AUTO_EXPENSE_AMOUNT,
                category=AUTO_EXPENSE_CATEGORY,
                note=AUTO_EXPENSE_NOTE,
                occurred_on=today,
            )
            created += 1

    logger.info(
        "ledger auto-expense %s: created=%d skipped=%d", today.isoformat(), created, skipped
    )
    return {"date": today.isoformat(), "created": created, "skipped": skipped}
