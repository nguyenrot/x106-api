"""Adds `users.google_sub` for "Sign in with Google".

Unlike the columns in 0002, this one is new everywhere — the legacy Go schema never had
it — so a plain AddField is safe on the production table too. NULL for every existing
row, and the unique index tolerates many NULLs in MySQL.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0003_create_user_m2m_tables'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='google_sub',
            field=models.CharField(blank=True, max_length=255, null=True, unique=True),
        ),
    ]
