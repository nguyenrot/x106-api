"""Create the M2M through tables Django auth/PermissionsMixin needs.

`migrate --fake-initial` only checks the primary model table (`users`), so the
M2M through tables `users_groups` and `users_user_permissions` were never
created on the legacy DB. Without them, ANY query that touches request.user
permissions (incl. `User.delete()` and Django admin checks) explodes with
`Table 'users_groups' doesn't exist`.

Idempotent guard via INFORMATION_SCHEMA so a fresh DB (where 0001_initial
created them already) is a no-op.
"""

from django.db import migrations


def _guard(table: str, ddl: str) -> str:
    return f"""
    SET @t := (
        SELECT COUNT(*) FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = '{table}'
    );
    SET @sql := IF(@t = 0, "{ddl}", 'SELECT 1');
    PREPARE stmt FROM @sql;
    EXECUTE stmt;
    DEALLOCATE PREPARE stmt;
    """


USERS_GROUPS_DDL = (
    "CREATE TABLE users_groups ("
    "id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,"
    "user_id CHAR(36) NOT NULL,"
    "group_id INT NOT NULL,"
    "UNIQUE KEY uq_users_groups (user_id, group_id),"
    "KEY idx_users_groups_group (group_id)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
)

USERS_PERMS_DDL = (
    "CREATE TABLE users_user_permissions ("
    "id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,"
    "user_id CHAR(36) NOT NULL,"
    "permission_id INT NOT NULL,"
    "UNIQUE KEY uq_users_perms (user_id, permission_id),"
    "KEY idx_users_perms_perm (permission_id)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_add_django_admin_columns"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[_guard("users_groups", USERS_GROUPS_DDL), _guard("users_user_permissions", USERS_PERMS_DDL)],
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
