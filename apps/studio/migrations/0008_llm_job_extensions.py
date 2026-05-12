from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("studio", "0007_seed_prompt_v1"),
    ]

    operations = [
        migrations.AddField(
            model_name="llmjob",
            name="celery_task_id",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="llmjob",
            name="prompt_version_id",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="llmjob",
            index=models.Index(
                fields=["prompt_version_id"],
                name="idx_llm_jobs_prompt_ver",
            ),
        ),
    ]
