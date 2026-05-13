from django.db import migrations, models

import apps.core.ids


class Migration(migrations.Migration):

    dependencies = [
        ("studio", "0013_app_setting_changes"),
    ]

    operations = [
        migrations.CreateModel(
            name="LLMConversation",
            fields=[
                ("id", models.CharField(
                    default=apps.core.ids.new_id,
                    editable=False, max_length=36, primary_key=True, serialize=False,
                )),
                ("user_id", models.CharField(max_length=36)),
                ("title", models.CharField(blank=True, default="", max_length=120)),
                ("pinned", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "llm_conversations",
                "ordering": ["-updated_at"],
                "indexes": [
                    models.Index(fields=["user_id", "-updated_at"], name="idx_conv_user_updated"),
                ],
            },
        ),
        migrations.CreateModel(
            name="LLMConversationMessage",
            fields=[
                ("id", models.CharField(
                    default=apps.core.ids.new_id,
                    editable=False, max_length=36, primary_key=True, serialize=False,
                )),
                ("role", models.CharField(
                    choices=[("user", "User"), ("assistant", "Assistant"), ("system", "System")],
                    max_length=16,
                )),
                ("content", models.TextField()),
                ("scene_snapshot", models.JSONField(blank=True, null=True)),
                ("applied_scene", models.BooleanField(default=False)),
                ("job_id", models.CharField(blank=True, max_length=36, null=True)),
                ("error_kind", models.CharField(blank=True, max_length=40, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("conversation", models.ForeignKey(
                    db_column="conversation_id",
                    on_delete=models.deletion.CASCADE,
                    related_name="messages",
                    to="studio.llmconversation",
                )),
            ],
            options={
                "db_table": "llm_conversation_messages",
                "ordering": ["created_at"],
                "indexes": [
                    models.Index(fields=["conversation", "created_at"], name="idx_convmsg_conv_at"),
                ],
            },
        ),
    ]
