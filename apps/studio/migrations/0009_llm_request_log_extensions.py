from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("studio", "0008_llm_job_extensions"),
    ]

    operations = [
        migrations.AddField(
            model_name="llmrequestlog",
            name="http_status",
            field=models.SmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="llmrequestlog",
            name="prompt_version_id",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="llmrequestlog",
            name="cost_cents",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="llmrequestlog",
            index=models.Index(
                fields=["status", "created_at"],
                name="idx_llm_logs_status_created",
            ),
        ),
        migrations.AddIndex(
            model_name="llmrequestlog",
            index=models.Index(
                fields=["model", "created_at"],
                name="idx_llm_logs_model_created",
            ),
        ),
    ]
