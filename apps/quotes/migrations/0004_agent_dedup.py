"""Add dedup_hash + QuoteAgentRun. Backfills hashes for existing rows.

Why: the daily quotes agent needs idempotency at the DB layer — if it
re-posts the same famous quote (network retry, bug, etc.), MySQL rejects
it with IntegrityError instead of creating a dupe. We use a partial unique
constraint so legacy rows that can't be hashed (empty body) stay NULL and
don't fight the constraint.
"""

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models

import apps.core.ids
import apps.quotes.models


def backfill_dedup_hash(apps_registry, schema_editor):
    Quote = apps_registry.get_model("quotes", "Quote")
    for q in Quote.objects.all().iterator():
        h = apps.quotes.models.compute_dedup_hash(q.body, q.author or "")
        if h and q.dedup_hash != h:
            q.dedup_hash = h
            q.save(update_fields=["dedup_hash"])


def noop_reverse(apps_registry, schema_editor):
    # Clearing hashes back to NULL on reverse is harmless.
    Quote = apps_registry.get_model("quotes", "Quote")
    Quote.objects.update(dedup_hash=None)


class Migration(migrations.Migration):

    dependencies = [
        ("quotes", "0003_body_to_bilingual"),
    ]

    operations = [
        migrations.AddField(
            model_name="quote",
            name="dedup_hash",
            field=models.CharField(
                blank=True, db_index=True, max_length=64, null=True
            ),
        ),
        migrations.RunPython(backfill_dedup_hash, noop_reverse),
        migrations.AddConstraint(
            model_name="quote",
            constraint=models.UniqueConstraint(
                fields=("dedup_hash",),
                name="uq_quotes_dedup_hash",
                condition=models.Q(dedup_hash__isnull=False),
            ),
        ),
        migrations.CreateModel(
            name="QuoteAgentRun",
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
                    "service_name",
                    models.CharField(
                        db_index=True, default="quotes-agent", max_length=64
                    ),
                ),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("started", "started"),
                            ("succeeded", "succeeded"),
                            ("skipped", "skipped"),
                            ("duplicate", "duplicate"),
                            ("failed", "failed"),
                        ],
                        default="started",
                        max_length=16,
                    ),
                ),
                ("theme_slug", models.CharField(blank=True, default="", max_length=64)),
                ("error_message", models.TextField(blank=True, default="")),
                ("extras", models.JSONField(blank=True, default=dict)),
                (
                    "quote",
                    models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="agent_runs",
                        to="quotes.quote",
                    ),
                ),
            ],
            options={
                "db_table": "quote_agent_runs",
                "ordering": ["-started_at"],
            },
        ),
        migrations.AddIndex(
            model_name="quoteagentrun",
            index=models.Index(
                fields=["status", "started_at"], name="ix_qar_status_started"
            ),
        ),
    ]
