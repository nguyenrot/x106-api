from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("studio", "0002_artwork_share_token"),
    ]

    operations = [
        migrations.AddField(
            model_name="llmjob",
            name="result_message",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="llmjob",
            name="mode",
            field=models.CharField(
                choices=[
                    ("random", "Random"),
                    ("polish", "Polish"),
                    ("remix", "Remix"),
                    ("chat", "Chat"),
                ],
                max_length=16,
            ),
        ),
    ]
