from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("console", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="consolemessage",
            name="reasoning_content",
            field=models.TextField(blank=True, default=""),
        ),
    ]
