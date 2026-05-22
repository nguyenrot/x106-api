"""Drop ConsoleMessage.reasoning_content (DeepSeek-only field) and flip the
default ai_model setting to gemini-2.5-flash. Existing rows that still hold
an OpenCode Zen model name (`deepseek-*`, `big-pickle`, etc.) are migrated
forward; rows already on a Gemini model are left untouched."""

from django.db import migrations, models


_GEMINI_DEFAULT = "gemini-2.5-flash"
_GEMINI_ALLOWED = ("gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite")


def _flip_ai_model(apps, schema_editor):
    ConsoleSetting = apps.get_model("console", "ConsoleSetting")
    try:
        row = ConsoleSetting.objects.get(pk="console.ai_model")
    except ConsoleSetting.DoesNotExist:
        return
    if row.value not in _GEMINI_ALLOWED:
        row.value = _GEMINI_DEFAULT
        row.save(update_fields=["value", "updated_at"])


def _noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("console", "0002_consolemessage_reasoning_content")]

    operations = [
        migrations.RemoveField(
            model_name="consolemessage",
            name="reasoning_content",
        ),
        migrations.RunPython(_flip_ai_model, _noop_reverse),
    ]
