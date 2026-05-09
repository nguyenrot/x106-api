"""Constants for the `app_settings` rows used by the studio app.

Mirrors internal/service/settings.go — same keys, same semantics, so admin UI
edits stored by the Go service continue to work after the Django cutover."""

from __future__ import annotations

from django.conf import settings

from .models import AppSetting

SETTING_LLM_SYSTEM_PROMPT = "llm.system_prompt"
SETTING_LLM_DAILY_LIMIT = "llm.daily_limit"
SETTING_LLM_ENABLED = "llm.enabled"
SETTING_LLM_MODEL = "llm.model"

ALLOWED_LLM_MODELS = (
    "deepseek-v4-pro",
    "deepseek-v4-flash",
)


def get_setting(key: str) -> str:
    row = AppSetting.objects.filter(name=key).first()
    return row.value if row else ""


def set_setting(key: str, value: str) -> None:
    AppSetting.objects.update_or_create(name=key, defaults={"value": value})


def delete_setting(key: str) -> None:
    AppSetting.objects.filter(name=key).delete()


def is_allowed_model(name: str) -> bool:
    return name in ALLOWED_LLM_MODELS


def effective_daily_limit() -> int:
    raw = get_setting(SETTING_LLM_DAILY_LIMIT).strip()
    if raw:
        try:
            n = int(raw)
            if n > 0:
                return n
        except ValueError:
            pass
    return int(settings.LLM_DAILY_LIMIT)


def llm_enabled() -> bool:
    return get_setting(SETTING_LLM_ENABLED).strip() != "off"


def effective_model() -> str:
    stored = get_setting(SETTING_LLM_MODEL).strip()
    if stored and is_allowed_model(stored):
        return stored
    if settings.DEEPSEEK_MODEL:
        return settings.DEEPSEEK_MODEL
    return ALLOWED_LLM_MODELS[0]
