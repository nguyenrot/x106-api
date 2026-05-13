"""Seed llm_models from the hardcoded catalog + existing AppSetting choices.

After this runs, services/model_catalog.py reads from DB (cache TTL 60s);
the constant LLM_MODEL_CATALOG becomes the boot-safe fallback only. The
AppSetting keys (llm.pro_model / llm.flash_model / llm.allowed_pro_models /
llm.allowed_flash_models) are kept for one deploy cycle to support rollback,
then dropped in a follow-up migration.

Sensible badges + descriptions are pre-filled for the 16 known models so the
user picker is informative on day-one. Admin can edit any of these via the
upcoming Phase 4.5 admin UI.
"""

from django.db import migrations

# Inline catalog with per-model presets. Keeping it inline (instead of importing
# from services/model_catalog.py) prevents future code-side renames from
# corrupting old migrations.
SEED_ROWS = [
    # (slug, display_name, provider, remote_id, role, description, speed, quality, cost,
    #  prompt_cents_per_mtok, completion_cents_per_mtok)
    (
        "deepseek-v4-flash", "DeepSeek V4 Flash", "deepseek", "deepseek-v4-flash", "flash",
        "Fast intent classifier — cheap, used as the chat router.",
        "fast", "good", "cheap", 14, 28,
    ),
    (
        "deepseek-v4-pro", "DeepSeek V4 Pro", "deepseek", "deepseek-v4-pro", "pro",
        "Reasoning model, draws complex scenes precisely. Default.",
        "slow", "best", "normal", 55, 219,
    ),
    (
        "opencode-go/glm-5", "GLM-5 (OpenCode)", "opencode_openai", "glm-5", "pro",
        "Solid scene generator — quick, low cost.",
        "fast", "great", "cheap", None, None,
    ),
    (
        "opencode-go/glm-5.1", "GLM-5.1 (OpenCode)", "opencode_openai", "glm-5.1", "pro",
        "GLM 5.1 — improved reasoning over 5.0.",
        "normal", "great", "cheap", None, None,
    ),
    (
        "opencode-go/kimi-k2.5", "Kimi K2.5 (OpenCode)", "opencode_openai", "kimi-k2.5", "pro",
        "Kimi K2.5 — balanced quality / speed.",
        "fast", "great", "cheap", None, None,
    ),
    (
        "opencode-go/kimi-k2.6", "Kimi K2.6 (OpenCode)", "opencode_openai", "kimi-k2.6", "pro",
        "Fast and tidy — saves your daily quota.",
        "fast", "great", "cheap", None, None,
    ),
    (
        "opencode-go/deepseek-v4-flash", "DeepSeek V4 Flash (OpenCode)",
        "opencode_openai", "deepseek-v4-flash", "flash",
        "DeepSeek Flash routed through OpenCode.",
        "fast", "good", "cheap", None, None,
    ),
    (
        "opencode-go/deepseek-v4-pro", "DeepSeek V4 Pro (OpenCode)",
        "opencode_openai", "deepseek-v4-pro", "pro",
        "DeepSeek Pro routed through OpenCode.",
        "slow", "best", "normal", None, None,
    ),
    (
        "opencode-go/qwen3.5-plus", "Qwen3.5 Plus (OpenCode)",
        "opencode_openai", "qwen3.5-plus", "both",
        "Qwen 3.5 Plus — flexible for both routing and drawing.",
        "normal", "great", "normal", None, None,
    ),
    (
        "opencode-go/qwen3.6-plus", "Qwen3.6 Plus (OpenCode)",
        "opencode_openai", "qwen3.6-plus", "pro",
        "Qwen 3.6 Plus — Qwen team's latest scene generator.",
        "normal", "great", "normal", None, None,
    ),
    (
        "opencode-go/mimo-v2-omni", "MiMo V2 Omni (OpenCode)",
        "opencode_openai", "mimo-v2-omni", "flash",
        "MiMo V2 Omni — router-tier.",
        "fast", "good", "cheap", None, None,
    ),
    (
        "opencode-go/mimo-v2-pro", "MiMo V2 Pro (OpenCode)",
        "opencode_openai", "mimo-v2-pro", "pro",
        "MiMo V2 Pro — scene generator.",
        "normal", "great", "normal", None, None,
    ),
    (
        "opencode-go/mimo-v2.5", "MiMo V2.5 (OpenCode)",
        "opencode_openai", "mimo-v2.5", "flash",
        "MiMo V2.5 — router successor.",
        "fast", "good", "cheap", None, None,
    ),
    (
        "opencode-go/mimo-v2.5-pro", "MiMo V2.5 Pro (OpenCode)",
        "opencode_openai", "mimo-v2.5-pro", "pro",
        "MiMo V2.5 Pro — newer scene generator.",
        "normal", "great", "normal", None, None,
    ),
    (
        "opencode-go/hy3-preview", "HY3 Preview (OpenCode)",
        "opencode_openai", "hy3-preview", "pro",
        "HY3 Preview — experimental, may be unstable.",
        "slow", "best", "premium", None, None,
    ),
    (
        "opencode-go/minimax-m2.5", "MiniMax M2.5 (OpenCode)",
        "opencode_anthropic", "minimax-m2.5", "pro",
        "MiniMax M2.5 via Anthropic-compatible endpoint.",
        "slow", "best", "premium", None, None,
    ),
    (
        "opencode-go/minimax-m2.7", "MiniMax M2.7 (OpenCode)",
        "opencode_anthropic", "minimax-m2.7", "pro",
        "MiniMax M2.7 — newest Anthropic-compat option.",
        "slow", "best", "premium", None, None,
    ),
]


def seed_models(apps, schema_editor):
    LLMModel = apps.get_model("studio", "LLMModel")
    AppSetting = apps.get_model("studio", "AppSetting")

    # Read existing admin choices to pre-populate is_default_* + allowed_for_users.
    def _setting(key: str) -> str:
        row = AppSetting.objects.filter(name=key).first()
        return (row.value or "").strip() if row else ""

    default_pro = _setting("llm.pro_model") or _setting("llm.model") or "deepseek-v4-pro"
    default_flash = _setting("llm.flash_model") or "deepseek-v4-flash"

    import json
    def _parse_list(key: str) -> set[str]:
        raw = _setting(key)
        if not raw:
            return set()
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return {s for s in parsed if isinstance(s, str)}
        except json.JSONDecodeError:
            pass
        return set()

    allowed_pro = _parse_list("llm.allowed_pro_models")
    allowed_flash = _parse_list("llm.allowed_flash_models")

    # Fallback if admin never edited allow-lists: default to DeepSeek-only
    # for backward-compat with the previous hardcoded behavior.
    if not allowed_pro:
        allowed_pro = {"deepseek-v4-pro"}
    if not allowed_flash:
        allowed_flash = {"deepseek-v4-flash"}

    for sort_order, row in enumerate(SEED_ROWS):
        (
            slug, display_name, provider, remote_id, role, description,
            speed_badge, quality_badge, cost_badge,
            prompt_rate, completion_rate,
        ) = row
        allowed = (
            slug in allowed_pro if role in ("pro", "both")
            else slug in allowed_flash
        )
        # Pro/both models can be the default pro; flash/both can be default flash.
        is_default_pro = (
            slug == default_pro and role in ("pro", "both")
        )
        is_default_flash = (
            slug == default_flash and role in ("flash", "both")
        )
        LLMModel.objects.create(
            slug=slug,
            display_name=display_name,
            provider=provider,
            remote_id=remote_id,
            role=role,
            description=description,
            speed_badge=speed_badge,
            quality_badge=quality_badge,
            cost_badge=cost_badge,
            enabled=True,
            is_default_pro=is_default_pro,
            is_default_flash=is_default_flash,
            allowed_for_users=allowed,
            deprecated=False,
            beta=False,
            prompt_cents_per_mtok=prompt_rate,
            completion_cents_per_mtok=completion_rate,
            sort_order=sort_order * 10,  # leave gaps for manual reordering
        )


def unseed(apps, schema_editor):
    LLMModel = apps.get_model("studio", "LLMModel")
    LLMModel.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("studio", "0010_llm_models_table"),
    ]

    operations = [
        migrations.RunPython(seed_models, unseed),
    ]
