"""Convert Quote.body from TextField to JSONField holding {en, vi} bilingual.

Existing rows are wrapped based on each row's `language` value:
  language='en' → body = {"en": <existing text>, "vi": ""}
  otherwise     → body = {"vi": <existing text>, "en": ""}

This preserves all current text and lets the frontend show the available
language with a fallback when the active UI language has no translation.
"""

import json

import apps.quotes.models
from django.db import migrations, models


def wrap_body_as_json(apps_registry, schema_editor):
    Quote = apps_registry.get_model("quotes", "Quote")
    for q in Quote.objects.all().iterator():
        old = q.body if isinstance(q.body, str) else (q.body or "")

        # If already JSON-shaped with en/vi keys, skip (re-run safety).
        if isinstance(old, str) and old.startswith("{"):
            try:
                parsed = json.loads(old)
                if isinstance(parsed, dict) and ("en" in parsed or "vi" in parsed):
                    continue
            except json.JSONDecodeError:
                pass

        text = old if isinstance(old, str) else ""
        lang = (q.language or "vi").lower()
        if lang == "en":
            wrapped = {"en": text, "vi": ""}
        else:
            wrapped = {"vi": text, "en": ""}
        q.body = json.dumps(wrapped, ensure_ascii=False)
        q.save(update_fields=["body"])


def unwrap_body_back_to_text(apps_registry, schema_editor):
    Quote = apps_registry.get_model("quotes", "Quote")
    for q in Quote.objects.all().iterator():
        raw = q.body
        if isinstance(raw, dict):
            parsed = raw
        elif isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue
        else:
            continue
        if not isinstance(parsed, dict):
            continue
        lang = (q.language or "vi").lower()
        q.body = parsed.get(lang) or parsed.get("vi") or parsed.get("en") or ""
        q.save(update_fields=["body"])


class Migration(migrations.Migration):

    dependencies = [
        ("quotes", "0002_drop_baogia"),
    ]

    operations = [
        # Phase 1: wrap each row's text body into a JSON-encoded {en, vi} dict.
        # Column is still TEXT so the JSON string fits as raw text.
        migrations.RunPython(wrap_body_as_json, unwrap_body_back_to_text),
        # Phase 2: switch the column type to JSON. MySQL parses the existing
        # text (now valid JSON) without data loss.
        migrations.AlterField(
            model_name="quote",
            name="body",
            field=models.JSONField(default=apps.quotes.models._empty_body),
        ),
    ]
