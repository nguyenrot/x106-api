"""Thêm full log columns vào QuoteAgentRun: prompt, raw response, parsed
JSON, validation error, timings. Cho admin UI xem trace mỗi lần agent chạy.

Tất cả nullable / blank-default để không cần backfill — legacy rows giữ
nguyên, rows mới có data đầy đủ.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("quotes", "0004_agent_dedup"),
    ]

    operations = [
        migrations.AddField(
            model_name="quoteagentrun",
            name="prompt",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="quoteagentrun",
            name="agy_response_raw",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="quoteagentrun",
            name="agy_response_parsed",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="quoteagentrun",
            name="validation_error",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="quoteagentrun",
            name="duration_ms",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="quoteagentrun",
            name="agy_duration_ms",
            field=models.IntegerField(blank=True, null=True),
        ),
    ]
