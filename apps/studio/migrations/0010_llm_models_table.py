from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("studio", "0009_llm_request_log_extensions"),
    ]

    operations = [
        migrations.CreateModel(
            name="LLMModel",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("slug", models.CharField(max_length=80, unique=True)),
                ("display_name", models.CharField(max_length=80)),
                (
                    "provider",
                    models.CharField(
                        choices=[
                            ("deepseek", "Deepseek"),
                            ("opencode_openai", "Opencode Openai"),
                            ("opencode_anthropic", "Opencode Anthropic"),
                        ],
                        max_length=32,
                    ),
                ),
                ("remote_id", models.CharField(max_length=120)),
                (
                    "role",
                    models.CharField(
                        choices=[("flash", "Flash"), ("pro", "Pro"), ("both", "Both")],
                        max_length=8,
                    ),
                ),
                ("description", models.CharField(blank=True, default="", max_length=240)),
                ("speed_badge", models.CharField(blank=True, default="", max_length=16)),
                ("quality_badge", models.CharField(blank=True, default="", max_length=16)),
                ("cost_badge", models.CharField(blank=True, default="", max_length=16)),
                ("enabled", models.BooleanField(default=True)),
                ("is_default_pro", models.BooleanField(default=False)),
                ("is_default_flash", models.BooleanField(default=False)),
                ("allowed_for_users", models.BooleanField(default=False)),
                ("deprecated", models.BooleanField(default=False)),
                ("beta", models.BooleanField(default=False)),
                ("prompt_cents_per_mtok", models.IntegerField(blank=True, null=True)),
                ("completion_cents_per_mtok", models.IntegerField(blank=True, null=True)),
                ("max_tokens_override", models.IntegerField(blank=True, null=True)),
                ("sort_order", models.SmallIntegerField(default=100)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "llm_models",
                "ordering": ["sort_order", "display_name"],
                "indexes": [
                    models.Index(
                        fields=["enabled", "allowed_for_users"],
                        name="idx_llmmodel_listing",
                    ),
                    models.Index(fields=["role"], name="idx_llmmodel_role"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("is_default_pro", True)),
                        fields=("is_default_pro",),
                        name="uq_llmmodel_default_pro",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(("is_default_flash", True)),
                        fields=("is_default_flash",),
                        name="uq_llmmodel_default_flash",
                    ),
                ],
            },
        ),
    ]
