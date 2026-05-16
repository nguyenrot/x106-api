"""Backfill default categories for every existing LedgerAccount.

Idempotent: skips accounts that already have any category rows so re-running
(or running on dev after a fresh seed) doesn't duplicate.
"""

from __future__ import annotations

from django.db import migrations

from apps.ledger.defaults import DEFAULT_CATEGORIES


def seed_existing_accounts(apps, schema_editor):
    LedgerAccount = apps.get_model("ledger", "LedgerAccount")
    LedgerCategoryRow = apps.get_model("ledger", "LedgerCategoryRow")

    accounts = LedgerAccount.objects.all()
    for account in accounts:
        if LedgerCategoryRow.objects.filter(account=account).exists():
            continue
        LedgerCategoryRow.objects.bulk_create(
            [
                LedgerCategoryRow(
                    account=account,
                    kind=kind,
                    slug=slug,
                    name=name,
                    color=color,
                    position=position,
                )
                for kind, slug, name, color, position in DEFAULT_CATEGORIES
            ]
        )


def unseed(apps, schema_editor):
    # Reverse migration: wipe all category rows. Safe because by reverting you
    # also drop the schema in 0002.
    LedgerCategoryRow = apps.get_model("ledger", "LedgerCategoryRow")
    LedgerCategoryRow.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("ledger", "0002_ledger_categories"),
    ]

    operations = [
        migrations.RunPython(seed_existing_accounts, reverse_code=unseed),
    ]
