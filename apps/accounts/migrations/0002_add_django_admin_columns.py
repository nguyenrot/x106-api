"""Conditionally adds the Django auth columns the legacy Go schema doesn't have.

Background: 0001_initial declares is_staff, is_superuser, is_active, last_login
as part of the User model. On a fresh DB, those columns are created with the
table — fine. On the production VPS, the `users` table predates Django and is
missing all four columns, so we run `migrate --fake-initial` (which marks 0001
applied without running CREATE TABLE) followed by this migration which actually
adds the columns. The IF-NOT-EXISTS guards via INFORMATION_SCHEMA so this is a
no-op on a fresh DB where 0001 already added them.
"""

from django.db import migrations


_GUARDED_ALTERS = (
    ("last_login", "ALTER TABLE users ADD COLUMN last_login DATETIME(6) NULL"),
    ("is_staff", "ALTER TABLE users ADD COLUMN is_staff TINYINT(1) NOT NULL DEFAULT 0"),
    ("is_superuser", "ALTER TABLE users ADD COLUMN is_superuser TINYINT(1) NOT NULL DEFAULT 0"),
    ("is_active", "ALTER TABLE users ADD COLUMN is_active TINYINT(1) NOT NULL DEFAULT 1"),
)


def _guarded_sql(column: str, alter: str) -> str:
    return f"""
    SET @col := (
        SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME   = 'users'
          AND COLUMN_NAME  = '{column}'
    );
    SET @sql := IF(@col = 0, "{alter}", 'SELECT 1');
    PREPARE stmt FROM @sql;
    EXECUTE stmt;
    DEALLOCATE PREPARE stmt;
    """


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[_guarded_sql(c, a) for c, a in _GUARDED_ALTERS],
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
