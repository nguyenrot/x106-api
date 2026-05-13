"""Phase 3.4 — request_payload->>'userMessage' search.

Adds a GENERATED ALWAYS STORED column extracting the user message string
from request_payload JSON + FULLTEXT index for fast content search in admin.

MySQL 8 supports FULLTEXT on InnoDB STORED generated columns. The column
is read-only from the app's perspective — admin queries it via raw SQL
(MATCH...AGAINST) or LIKE fallback for short queries.
"""

from django.db import migrations, models

CREATE_COL_SQL = """
ALTER TABLE llm_request_logs
  ADD COLUMN user_message_text TEXT
  GENERATED ALWAYS AS (
    JSON_UNQUOTE(JSON_EXTRACT(request_payload, '$.userMessage'))
  ) STORED
"""

DROP_COL_SQL = "ALTER TABLE llm_request_logs DROP COLUMN user_message_text"

CREATE_INDEX_SQL = (
    "ALTER TABLE llm_request_logs "
    "ADD FULLTEXT INDEX ft_user_message (user_message_text)"
)

DROP_INDEX_SQL = "ALTER TABLE llm_request_logs DROP INDEX ft_user_message"


class Migration(migrations.Migration):

    dependencies = [
        ("studio", "0011_seed_llm_models"),
    ]

    operations = [
        # Django state mirrors the model field added in this same change so
        # `--check` won't ask for a re-migration. The actual DDL is raw because
        # Django doesn't model GENERATED ALWAYS columns.
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(CREATE_COL_SQL, reverse_sql=DROP_COL_SQL),
                migrations.RunSQL(CREATE_INDEX_SQL, reverse_sql=DROP_INDEX_SQL),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="llmrequestlog",
                    name="user_message_text",
                    field=models.TextField(blank=True, editable=False, null=True),
                ),
            ],
        ),
    ]
