import apps.core.ids
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        # Ensure the legacy `artworks` table (dropped on 2026-05-22) is gone
        # before this migration recreates it.
        ("core", "0001_drop_legacy_ai"),
    ]

    operations = [
        migrations.CreateModel(
            name="Artwork",
            fields=[
                ("id", models.CharField(default=apps.core.ids.new_id, editable=False, max_length=36, primary_key=True, serialize=False)),
                ("kind", models.CharField(choices=[("favorite", "favorite"), ("upload", "upload"), ("snapshot", "snapshot")], default="snapshot", max_length=20)),
                ("source_id", models.CharField(blank=True, max_length=64, null=True)),
                ("title", models.CharField(max_length=120)),
                ("prompt", models.TextField(blank=True, default="")),
                ("style", models.CharField(default="3d-art-studio", max_length=64)),
                ("palette", models.CharField(default="", max_length=64)),
                ("seed", models.BigIntegerField(default=0)),
                ("settings", models.JSONField(blank=True, default=dict)),
                ("scene", models.JSONField(blank=True, default=dict)),
                ("thumbnail_data_url", models.TextField()),
                ("asset_data_url", models.TextField(blank=True, null=True)),
                ("share_token", models.CharField(blank=True, max_length=32, null=True, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(
                        db_column="user_id",
                        db_constraint=False,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="artworks",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "artworks",
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["user", "-created_at"], name="idx_artworks_user_created"),
                ],
            },
        ),
    ]
