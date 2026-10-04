"""Drop the tables of two removed apps (2026-10-04).

- `apps.console` — the old AI ops chat (Gemini + paramiko SSH to x106-ops).
  Its UI left admin in the console-first rebuild; the terminal itself lives in
  `terminal_ws/` + `apps.ops` and keeps no tables here.
- `apps.quotes` — quotes.kynguyen.cc, retired 2026-09-28 (host is a 301).

Backup taken on the VPS before deploying:
/root/backups/console-quotes-before-removal-20261004.sql.gz
"""

from django.db import migrations

TABLES = [
    "console_execs",
    "console_messages",
    "console_sessions",
    "console_settings",
    "quote_favorites",
    "quote_agent_runs",
    "quotes",
]


def drop_tables(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
        for table in TABLES:
            cursor.execute(f"DROP TABLE IF EXISTS `{table}`")
        cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
        cursor.execute("DELETE FROM django_migrations WHERE app IN ('console', 'quotes')")


class Migration(migrations.Migration):
    dependencies = [("core", "0001_drop_legacy_ai")]
    operations = [migrations.RunPython(drop_tables, migrations.RunPython.noop)]
