import apps.core.ids
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ledger", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="LedgerCategoryRow",
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
                ("slug", models.CharField(max_length=64)),
                ("name", models.CharField(max_length=40)),
                ("color", models.CharField(default="#94a3b8", max_length=7)),
                ("position", models.PositiveIntegerField(default=0)),
                ("is_archived", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "account",
                    models.ForeignKey(
                        db_column="account_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="categories",
                        to="ledger.ledgeraccount",
                    ),
                ),
            ],
            options={
                "db_table": "ledger_categories",
                "ordering": ["kind", "position", "created_at"],
                "indexes": [
                    models.Index(
                        fields=["account", "kind", "is_archived"],
                        name="idx_ledger_cat_acct_kind",
                    ),
                    models.Index(
                        fields=["account", "kind", "slug"],
                        name="idx_ledger_cat_slug",
                    ),
                ],
            },
        ),
        migrations.AlterField(
            model_name="ledgertransaction",
            name="category",
            field=models.CharField(default="other", max_length=64),
        ),
    ]
