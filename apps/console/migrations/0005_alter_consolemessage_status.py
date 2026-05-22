"""Drop `awaiting_confirm` from ConsoleMessage.status choices.

That state existed because the old AI agent had a human-approve gate. agy
runs tools autonomously, so the state is gone. Migrate any leftover rows
forward to `done` — the message content was real, just the lifecycle peg
is no longer meaningful."""

from django.db import migrations, models


def _flip_awaiting_confirm(apps, schema_editor):
    ConsoleMessage = apps.get_model("console", "ConsoleMessage")
    ConsoleMessage.objects.filter(status="awaiting_confirm").update(status="done")


def _noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('console', '0004_drop_consoleexec_switch_to_agy'),
    ]

    operations = [
        migrations.RunPython(_flip_awaiting_confirm, _noop_reverse),
        migrations.AlterField(
            model_name='consolemessage',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'pending'),
                    ('streaming', 'streaming'),
                    ('done', 'done'),
                    ('failed', 'failed'),
                    ('canceled', 'canceled'),
                ],
                default='done',
                max_length=20,
            ),
        ),
    ]
