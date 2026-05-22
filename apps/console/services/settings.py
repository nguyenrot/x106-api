"""Thin helper around the `console_settings` key/value table.

Reads fall through to `DEFAULTS` (declared in `apps.console.settings_keys`)
when the row is missing, so a fresh DB works without seeding.
"""

from __future__ import annotations

from apps.console.models import ConsoleSetting
from apps.console.settings_keys import DEFAULTS


def get_setting(key: str) -> str:
    try:
        return ConsoleSetting.objects.get(pk=key).value
    except ConsoleSetting.DoesNotExist:
        return DEFAULTS.get(key, "")


def set_setting(key: str, value: str) -> None:
    ConsoleSetting.objects.update_or_create(pk=key, defaults={"value": value})


def get_bool(key: str) -> bool:
    return get_setting(key).strip().lower() in {"true", "1", "yes", "on"}


def get_int(key: str) -> int:
    raw = get_setting(key).strip()
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(DEFAULTS.get(key, "0") or "0")
