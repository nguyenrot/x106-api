from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("studio", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="artwork",
            name="share_token",
            field=models.CharField(blank=True, max_length=48, null=True, unique=True),
        ),
    ]
