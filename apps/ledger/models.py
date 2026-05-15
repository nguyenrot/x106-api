"""Ledger — simple personal expense/income tracker for ledger.kynguyen.cc.

Auth model is intentionally different from the rest of the API: one opaque token
per account, stored client-side in localStorage forever. The server only keeps
the SHA-256 hash of each token, so a DB leak can't replay tokens elsewhere.
"""

from __future__ import annotations

from django.db import models

from apps.core.ids import new_id


class LedgerCategory(models.TextChoices):
    FOOD = "food", "Ăn uống"
    TRANSPORT = "transport", "Di chuyển"
    SHOPPING = "shopping", "Mua sắm"
    BILLS = "bills", "Hóa đơn"
    ENTERTAINMENT = "entertainment", "Giải trí"
    HEALTH = "health", "Sức khỏe"
    SALARY = "salary", "Lương"
    BONUS = "bonus", "Thưởng"
    OTHER = "other", "Khác"


class LedgerKind(models.TextChoices):
    INCOME = "income", "Thu"
    EXPENSE = "expense", "Chi"


class LedgerAccount(models.Model):
    id = models.CharField(primary_key=True, max_length=36, default=new_id, editable=False)
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ledger_accounts"
        ordering = ["-created_at"]

    @property
    def is_authenticated(self) -> bool:
        # Duck-type as an authenticated principal so DRF's IsAuthenticated
        # treats a resolved LedgerAccount as a logged-in user.
        return True

    @property
    def is_anonymous(self) -> bool:
        return False

    def __str__(self) -> str:  # pragma: no cover
        return f"LedgerAccount<{self.id}>"


class LedgerTransaction(models.Model):
    id = models.CharField(primary_key=True, max_length=36, default=new_id, editable=False)
    account = models.ForeignKey(
        LedgerAccount,
        on_delete=models.CASCADE,
        db_column="account_id",
        related_name="transactions",
    )
    kind = models.CharField(max_length=16, choices=LedgerKind.choices)
    # VND, integer (Vietnamese đồng has no sub-units in practice).
    amount = models.BigIntegerField()
    category = models.CharField(
        max_length=32,
        choices=LedgerCategory.choices,
        default=LedgerCategory.OTHER,
    )
    note = models.CharField(max_length=255, blank=True, default="")
    occurred_on = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ledger_transactions"
        ordering = ["-occurred_on", "-created_at"]
        indexes = [
            models.Index(fields=["account", "occurred_on"], name="idx_ledger_acct_date"),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"LedgerTransaction<{self.id} {self.kind} {self.amount}>"
