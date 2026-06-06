"""Switch habits to ledger-style token auth with a SHARED account.

Re-parents Habit/HabitLog from the x106_session `user_id` to `account_id`
pointing at `ledger.LedgerAccount` (table `ledger_accounts`) — so one token
works on both /ledger/* and /habits/*. Existing rows are throwaway test data, so
both tables are cleared first, letting the new non-null `account` FK be added to
empty tables.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("habits", "0001_initial"),
        ("ledger", "0001_initial"),
    ]

    operations = [
        # Clear throwaway data so the non-null account FK can be added.
        migrations.RunSQL("DELETE FROM habit_logs;", migrations.RunSQL.noop),
        migrations.RunSQL("DELETE FROM habits;", migrations.RunSQL.noop),

        migrations.RemoveIndex(model_name="habit", name="idx_habits_user_archived"),
        migrations.RemoveIndex(model_name="habitlog", name="idx_habit_logs_user_date"),

        migrations.RemoveField(model_name="habit", name="user"),
        migrations.RemoveField(model_name="habitlog", name="user"),

        migrations.AddField(
            model_name="habit",
            name="account",
            field=models.ForeignKey(
                db_column="account_id",
                db_constraint=False,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="habits",
                to="ledger.ledgeraccount",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="habitlog",
            name="account",
            field=models.ForeignKey(
                db_column="account_id",
                db_constraint=False,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="habit_logs",
                to="ledger.ledgeraccount",
            ),
            preserve_default=False,
        ),

        migrations.AddIndex(
            model_name="habit",
            index=models.Index(fields=["account", "archived"], name="idx_habits_acct_archived"),
        ),
        migrations.AddIndex(
            model_name="habitlog",
            index=models.Index(fields=["account", "date"], name="idx_habit_logs_acct_date"),
        ),
    ]
