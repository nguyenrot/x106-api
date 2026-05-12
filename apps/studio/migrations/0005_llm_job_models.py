from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("studio", "0004_chat_only"),
    ]

    operations = [
        migrations.AddField(
            model_name="llmjob",
            name="flash_model",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="llmjob",
            name="pro_model",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
    ]
