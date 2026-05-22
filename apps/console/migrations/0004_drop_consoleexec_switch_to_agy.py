"""Drop the ConsoleExec table and obsolete ConsoleSetting rows.

The console feature now drives the `agy` Antigravity CLI directly. agy
handles tool calls and shell execution autonomously inside its own process,
so the human-approve pipeline (run_shell tool, ConsoleExec rows, danger
classifier, DESTROY phrase, max_agent_steps, command_timeout_sec, ai_model
allowlist) is gone.

This migration is **destructive** — the audit history in `console_execs`
will be lost. That's intentional; the rows were tied to the old approval
loop and don't map to agy's execution model.
"""

from django.db import migrations


_OBSOLETE_SETTING_KEYS = (
    "console.ai_model",
    "console.command_timeout_sec",
    "console.max_agent_steps",
    "console.destroy_phrase",
)


def _delete_obsolete_settings(apps, schema_editor):
    ConsoleSetting = apps.get_model("console", "ConsoleSetting")
    ConsoleSetting.objects.filter(key__in=_OBSOLETE_SETTING_KEYS).delete()


def _noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("console", "0003_drop_reasoning_content_switch_to_gemini")]

    operations = [
        migrations.DeleteModel(name="ConsoleExec"),
        migrations.RunPython(_delete_obsolete_settings, _noop_reverse),
    ]
