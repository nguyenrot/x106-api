"""Drop legacy AI infrastructure (studio + admin_art).

This migration removes the entire AI-art feature stack: model tables, settings
tables, and any migration-history rows belonging to the removed apps. It runs
exactly once in production — the next deploy after `apps.studio` and
`apps.admin_art` are removed from INSTALLED_APPS.
"""

from django.db import migrations

LEGACY_TABLES = [
    "artworks",
    "llm_jobs",
    "llm_usage",
    "llm_request_logs",
    "llm_conversations",
    "llm_conversation_messages",
    "llm_models",
    "llm_prompt_versions",
    "app_settings",
    "app_setting_changes",
]


def drop_legacy_tables(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
        for table in LEGACY_TABLES:
            cursor.execute(f"DROP TABLE IF EXISTS `{table}`")
        cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
        cursor.execute(
            "DELETE FROM django_migrations WHERE app IN ('studio', 'admin_art')"
        )


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [migrations.RunPython(drop_legacy_tables, migrations.RunPython.noop)]
