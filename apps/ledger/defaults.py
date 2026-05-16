"""Default categories seeded on every new LedgerAccount.

Slugs match the old hardcoded enum values (`food`, `transport`, …) so any
LedgerTransaction.category strings written before user-defined categories
existed still resolve to a row after the data migration runs.
"""

from __future__ import annotations

from typing import Sequence

# (kind, slug, name, color, position)
DEFAULT_CATEGORIES: Sequence[tuple[str, str, str, str, int]] = (
    ("expense", "food",          "Ăn uống",     "#f87171", 0),
    ("expense", "transport",     "Di chuyển",   "#38bdf8", 1),
    ("expense", "shopping",      "Mua sắm",     "#a78bfa", 2),
    ("expense", "bills",         "Hóa đơn",     "#fbbf24", 3),
    ("expense", "entertainment", "Giải trí",    "#f472b6", 4),
    ("expense", "health",        "Sức khỏe",    "#34d399", 5),
    ("expense", "other",         "Khác",        "#94a3b8", 6),
    ("income",  "salary",        "Lương",       "#34d399", 0),
    ("income",  "bonus",         "Thưởng",      "#fbbf24", 1),
    ("income",  "other",         "Khác",        "#94a3b8", 2),
)


def seed_default_categories(account, model=None) -> None:
    """Create the default category rows for a freshly minted account.

    `model` is injectable so this same function can be called from a data
    migration where `LedgerCategoryRow` must be resolved via apps.get_model().
    """
    if model is None:
        from .models import LedgerCategoryRow as model  # late import — avoid circular
    rows = [
        model(
            account=account,
            kind=kind,
            slug=slug,
            name=name,
            color=color,
            position=position,
        )
        for kind, slug, name, color, position in DEFAULT_CATEGORIES
    ]
    model.objects.bulk_create(rows)
