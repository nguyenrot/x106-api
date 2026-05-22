"""Initial schema for apps.console — VPS console + AI ops assistant."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

from apps.core.ids import new_id


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("core", "0001_drop_legacy_ai"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ConsoleSetting",
            fields=[
                ("key", models.CharField(max_length=64, primary_key=True, serialize=False)),
                ("value", models.TextField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "console_settings"},
        ),
        migrations.CreateModel(
            name="ConsoleSession",
            fields=[
                (
                    "id",
                    models.CharField(
                        default=new_id,
                        editable=False,
                        max_length=36,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("title", models.CharField(default="Cuộc trò chuyện mới", max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(
                        db_column="user_id",
                        db_constraint=False,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="console_sessions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "console_sessions",
                "indexes": [
                    models.Index(fields=["user", "-updated_at"], name="cs_user_updated_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="ConsoleMessage",
            fields=[
                (
                    "id",
                    models.CharField(
                        default=new_id,
                        editable=False,
                        max_length=36,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("user", "user"),
                            ("assistant", "assistant"),
                            ("system", "system"),
                        ],
                        max_length=16,
                    ),
                ),
                ("content", models.TextField(blank=True, default="")),
                ("step_count", models.PositiveIntegerField(default=0)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "pending"),
                            ("streaming", "streaming"),
                            ("awaiting_confirm", "awaiting_confirm"),
                            ("done", "done"),
                            ("failed", "failed"),
                            ("canceled", "canceled"),
                        ],
                        default="done",
                        max_length=20,
                    ),
                ),
                ("error_message", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "session",
                    models.ForeignKey(
                        db_column="session_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="messages",
                        to="console.consolesession",
                    ),
                ),
            ],
            options={
                "db_table": "console_messages",
                "indexes": [
                    models.Index(
                        fields=["session", "created_at"], name="cm_session_created_idx"
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="ConsoleExec",
            fields=[
                (
                    "id",
                    models.CharField(
                        default=new_id,
                        editable=False,
                        max_length=36,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("command", models.TextField()),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("user_direct", "user_direct"),
                            ("ai_proposed", "ai_proposed"),
                        ],
                        max_length=16,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("awaiting_confirm", "awaiting_confirm"),
                            ("approved", "approved"),
                            ("running", "running"),
                            ("done", "done"),
                            ("failed", "failed"),
                            ("canceled", "canceled"),
                            ("denied", "denied"),
                        ],
                        default="awaiting_confirm",
                        max_length=20,
                    ),
                ),
                (
                    "danger_level",
                    models.CharField(
                        choices=[
                            ("safe", "safe"),
                            ("write", "write"),
                            ("destructive", "destructive"),
                        ],
                        default="write",
                        max_length=16,
                    ),
                ),
                ("danger_reasons", models.JSONField(blank=True, default=list)),
                ("stdout", models.TextField(blank=True, default="")),
                ("stderr", models.TextField(blank=True, default="")),
                ("exit_code", models.IntegerField(blank=True, null=True)),
                ("latency_ms", models.IntegerField(blank=True, null=True)),
                ("error_message", models.TextField(blank=True, default="")),
                ("tool_call_id", models.CharField(blank=True, default="", max_length=128)),
                ("deny_reason", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                (
                    "session",
                    models.ForeignKey(
                        blank=True,
                        db_column="session_id",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="execs",
                        to="console.consolesession",
                    ),
                ),
                (
                    "message",
                    models.ForeignKey(
                        blank=True,
                        db_column="message_id",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="execs",
                        to="console.consolemessage",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        db_column="user_id",
                        db_constraint=False,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="console_execs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "console_execs",
                "indexes": [
                    models.Index(fields=["user", "-created_at"], name="ce_user_created_idx"),
                    models.Index(
                        fields=["session", "created_at"], name="ce_session_created_idx"
                    ),
                    models.Index(
                        fields=["status", "started_at"], name="ce_status_started_idx"
                    ),
                ],
            },
        ),
    ]
