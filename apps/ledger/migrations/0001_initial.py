import apps.core.ids
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="LedgerAccount",
            fields=[
                (
                    "id",
                    models.CharField(
                        default=apps.core.ids.new_id,
                        editable=False,
                        max_length=36,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("token_hash", models.CharField(db_index=True, max_length=64, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "ledger_accounts",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="LedgerTransaction",
            fields=[
                (
                    "id",
                    models.CharField(
                        default=apps.core.ids.new_id,
                        editable=False,
                        max_length=36,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[("income", "Thu"), ("expense", "Chi")],
                        max_length=16,
                    ),
                ),
                ("amount", models.BigIntegerField()),
                (
                    "category",
                    models.CharField(
                        choices=[
                            ("food", "Ăn uống"),
                            ("transport", "Di chuyển"),
                            ("shopping", "Mua sắm"),
                            ("bills", "Hóa đơn"),
                            ("entertainment", "Giải trí"),
                            ("health", "Sức khỏe"),
                            ("salary", "Lương"),
                            ("bonus", "Thưởng"),
                            ("other", "Khác"),
                        ],
                        default="other",
                        max_length=32,
                    ),
                ),
                ("note", models.CharField(blank=True, default="", max_length=255)),
                ("occurred_on", models.DateField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "account",
                    models.ForeignKey(
                        db_column="account_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="transactions",
                        to="ledger.ledgeraccount",
                    ),
                ),
            ],
            options={
                "db_table": "ledger_transactions",
                "ordering": ["-occurred_on", "-created_at"],
                "indexes": [
                    models.Index(
                        fields=["account", "occurred_on"], name="idx_ledger_acct_date"
                    )
                ],
            },
        ),
    ]
