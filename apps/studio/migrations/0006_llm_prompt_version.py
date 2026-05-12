from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("studio", "0005_llm_job_models"),
    ]

    operations = [
        migrations.CreateModel(
            name="LLMPromptVersion",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "kind",
                    models.CharField(
                        choices=[("chat", "Chat"), ("router", "Router")],
                        max_length=16,
                    ),
                ),
                ("body", models.TextField()),
                ("notes", models.CharField(blank=True, default="", max_length=240)),
                ("created_by", models.CharField(blank=True, default="", max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("is_active", models.BooleanField(default=False)),
            ],
            options={
                "db_table": "llm_prompt_versions",
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(
                        fields=["kind", "-created_at"],
                        name="idx_promptver_kind_created",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("is_active", True)),
                        fields=("kind",),
                        name="uq_promptver_active_per_kind",
                    ),
                ],
            },
        ),
    ]
