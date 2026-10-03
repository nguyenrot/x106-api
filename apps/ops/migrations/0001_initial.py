from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies: list = []

    operations = [
        migrations.CreateModel(
            name="OpsPref",
            fields=[
                ("key", models.CharField(max_length=64, primary_key=True, serialize=False)),
                ("data", models.JSONField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "ops_prefs"},
        ),
    ]
