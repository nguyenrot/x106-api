"""Seed the active chat + router prompts from the hardcoded Python constants.

After this runs, services.prompts.get_active_prompt() reads from DB; the
constants in prompts.py become the "v1 snapshot" frozen in source for
boot-safety fallback only.
"""

from django.db import migrations


def seed_initial_prompts(apps, schema_editor):
    LLMPromptVersion = apps.get_model("studio", "LLMPromptVersion")
    # Local import — prompts.py is plain Python, not a Django model, so it's
    # safe to import even from a data migration. We accept that future edits
    # to the constants won't reseed; that's intentional — once an admin edits
    # via the upcoming editor (Phase 3.1), the DB is the source of truth.
    from apps.studio.services.prompts import CHAT_ROUTER_PROMPT, CHAT_SYSTEM_PROMPT

    LLMPromptVersion.objects.create(
        kind="chat",
        body=CHAT_SYSTEM_PROMPT,
        notes="seeded from prompts.py CHAT_SYSTEM_PROMPT",
        created_by="migration:0007",
        is_active=True,
    )
    LLMPromptVersion.objects.create(
        kind="router",
        body=CHAT_ROUTER_PROMPT,
        notes="seeded from prompts.py CHAT_ROUTER_PROMPT",
        created_by="migration:0007",
        is_active=True,
    )


def unseed(apps, schema_editor):
    LLMPromptVersion = apps.get_model("studio", "LLMPromptVersion")
    LLMPromptVersion.objects.filter(created_by="migration:0007").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("studio", "0006_llm_prompt_version"),
    ]

    operations = [
        migrations.RunPython(seed_initial_prompts, unseed),
    ]
