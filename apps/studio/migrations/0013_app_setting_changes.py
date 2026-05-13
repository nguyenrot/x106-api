from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("studio", "0012_request_log_user_message_search"),
    ]

    operations = [
        migrations.CreateModel(
            name="AppSettingChange",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("setting_name", models.CharField(max_length=80)),
                ("old_value", models.TextField(blank=True, null=True)),
                ("new_value", models.TextField(blank=True, null=True)),
                ("changed_by", models.CharField(blank=True, default="", max_length=64)),
                ("changed_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "app_setting_changes",
                "ordering": ["-changed_at"],
                "indexes": [
                    models.Index(
                        fields=["setting_name", "-changed_at"],
                        name="idx_setchange_name_at",
                    ),
                ],
            },
        ),
    ]
