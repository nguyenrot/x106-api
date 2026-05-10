from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("studio", "0003_chat_mode"),
    ]

    operations = [
        migrations.AlterField(
            model_name="llmjob",
            name="mode",
            field=models.CharField(
                choices=[("chat", "Chat")],
                max_length=16,
            ),
        ),
    ]
