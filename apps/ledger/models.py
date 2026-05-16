"""Ledger — simple personal expense/income tracker for ledger.kynguyen.cc.

Auth model is intentionally different from the rest of the API: one opaque token
per account, stored client-side in localStorage forever. The server only keeps
the SHA-256 hash of each token, so a DB leak can't replay tokens elsewhere.
"""

from __future__ import annotations

from django.db import models

from apps.core.ids import new_id


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


class LedgerCategoryRow(models.Model):
    """Per-account, per-kind category. Replaces the previous static enum.

    `slug` is the stable identifier stored in LedgerTransaction.category — never
    changes after creation, so renames don't break transaction history. `name` is
    the display label the user can edit freely. Soft-deleted via `is_archived`
    so transactions that referenced the category still resolve.
    """

    id = models.CharField(primary_key=True, max_length=36, default=new_id, editable=False)
    account = models.ForeignKey(
        LedgerAccount,
        on_delete=models.CASCADE,
        db_column="account_id",
        related_name="categories",
    )
    kind = models.CharField(max_length=16, choices=LedgerKind.choices)
    slug = models.CharField(max_length=64)
    name = models.CharField(max_length=40)
    color = models.CharField(max_length=7, default="#94a3b8")  # hex like #f87171
    position = models.PositiveIntegerField(default=0)
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ledger_categories"
        ordering = ["kind", "position", "created_at"]
        indexes = [
            models.Index(fields=["account", "kind", "is_archived"], name="idx_ledger_cat_acct_kind"),
            models.Index(fields=["account", "kind", "slug"], name="idx_ledger_cat_slug"),
        ]


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
    # Stores the slug of a LedgerCategoryRow. No DB-level FK (dynamic per-account
    # categories rule out a global FK without a composite key) and no choices
    # constraint — validation happens at the serializer layer against the
    # account's own category list.
    category = models.CharField(max_length=64, default="other")
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
