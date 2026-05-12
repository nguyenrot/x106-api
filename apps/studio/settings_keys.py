"""Constants for the `app_settings` rows used by the studio app.

`llm.model` (single) is kept as a back-compat alias for `llm.pro_model`. New
deployments should write `llm.flash_model` / `llm.pro_model` separately, plus
allow-lists `llm.allowed_flash_models` / `llm.allowed_pro_models` (JSON arrays
of model ids) that the chat UI uses to populate the user-visible picker.
"""

from __future__ import annotations

import json
import logging

from django.conf import settings

from .models import AppSetting
from .services.model_catalog import (
    ModelSpec,
    all_models,
    get_model,
    is_known_model,
    models_for_role,
    supports_role,
)

log = logging.getLogger("x106.studio.settings_keys")

SETTING_LLM_DAILY_LIMIT = "llm.daily_limit"
SETTING_LLM_ENABLED = "llm.enabled"
SETTING_LLM_MODEL = "llm.model"  # legacy single-model setting (= pro_model)
SETTING_LLM_FLASH_MODEL = "llm.flash_model"
SETTING_LLM_PRO_MODEL = "llm.pro_model"
SETTING_LLM_ALLOWED_FLASH = "llm.allowed_flash_models"  # JSON array
SETTING_LLM_ALLOWED_PRO = "llm.allowed_pro_models"  # JSON array
SETTING_LLM_PRO_MAX_TOKENS = "llm.pro_max_tokens"
SETTING_LLM_FLASH_MAX_TOKENS = "llm.flash_max_tokens"

# Sane defaults. Pro model has 6500 (down from 32000): a 16-shape scene
# serializes ~2k tokens + ~4k headroom for the reasoning model's thinking.
# Router model returns a tiny JSON (~80 tokens of output) so 500 is generous.
# Both are clamped to (min, max) when reading the AppSetting override.
DEFAULT_PRO_MAX_TOKENS = 6500
DEFAULT_FLASH_MAX_TOKENS = 500
PRO_MAX_TOKENS_MIN, PRO_MAX_TOKENS_MAX = 256, 32000
FLASH_MAX_TOKENS_MIN, FLASH_MAX_TOKENS_MAX = 64, 2000

# Legacy constant kept so existing imports (e.g. admin_art) keep working.
# It now means "every model id the catalog knows about".
ALLOWED_LLM_MODELS = tuple(m.id for m in all_models())


def get_setting(key: str) -> str:
    row = AppSetting.objects.filter(name=key).first()
    return row.value if row else ""


def set_setting(key: str, value: str) -> None:
    AppSetting.objects.update_or_create(name=key, defaults={"value": value})


def delete_setting(key: str) -> None:
    AppSetting.objects.filter(name=key).delete()


def is_allowed_model(name: str) -> bool:
    """Compatibility helper — exists in catalog at all."""
    return is_known_model(name)


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


def _read_int_setting(key: str, default: int, lo: int, hi: int) -> int:
    raw = get_setting(key).strip()
    if not raw:
        return default
    try:
        n = int(raw)
    except ValueError:
        log.warning("setting %s is not a valid int: %r — falling back to %d", key, raw[:60], default)
        return default
    if n < lo or n > hi:
        log.warning("setting %s=%d outside [%d,%d] — falling back to %d", key, n, lo, hi, default)
        return default
    return n


def effective_pro_max_tokens() -> int:
    return _read_int_setting(
        SETTING_LLM_PRO_MAX_TOKENS,
        DEFAULT_PRO_MAX_TOKENS,
        PRO_MAX_TOKENS_MIN,
        PRO_MAX_TOKENS_MAX,
    )


def effective_flash_max_tokens() -> int:
    return _read_int_setting(
        SETTING_LLM_FLASH_MAX_TOKENS,
        DEFAULT_FLASH_MAX_TOKENS,
        FLASH_MAX_TOKENS_MIN,
        FLASH_MAX_TOKENS_MAX,
    )


# ─── Model defaults ──────────────────────────────────────────────────────────


def _read_model_setting(primary_key: str, fallback_keys: tuple[str, ...] = ()) -> str:
    raw = get_setting(primary_key).strip()
    if raw and is_known_model(raw):
        return raw
    for fk in fallback_keys:
        raw = get_setting(fk).strip()
        if raw and is_known_model(raw):
            return raw
    return ""


def effective_pro_model() -> str:
    stored = _read_model_setting(SETTING_LLM_PRO_MODEL, (SETTING_LLM_MODEL,))
    if stored and supports_role(stored, "pro"):
        return stored
    env_default = (settings.DEEPSEEK_MODEL or "").strip()
    if env_default and is_known_model(env_default) and supports_role(env_default, "pro"):
        return env_default
    return "deepseek-v4-pro"


def effective_flash_model() -> str:
    stored = _read_model_setting(SETTING_LLM_FLASH_MODEL)
    if stored and supports_role(stored, "flash"):
        return stored
    env_default = (settings.DEEPSEEK_FLASH_MODEL or "").strip()
    if env_default and is_known_model(env_default) and supports_role(env_default, "flash"):
        return env_default
    return "deepseek-v4-flash"


# Back-compat alias — code that used to import `effective_model` still works.
def effective_model() -> str:
    return effective_pro_model()


# ─── Allow-lists ─────────────────────────────────────────────────────────────


def _parse_allowed_setting(key: str) -> list[str]:
    raw = get_setting(key).strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("setting %s is not valid JSON: %r", key, raw[:120])
        return []
    if not isinstance(parsed, list):
        return []
    return [m for m in parsed if isinstance(m, str) and is_known_model(m)]


def _default_allow_list(role: str) -> list[str]:
    return [m.id for m in models_for_role(role) if m.provider == "deepseek"]


def allowed_pro_models() -> list[str]:
    stored = _parse_allowed_setting(SETTING_LLM_ALLOWED_PRO)
    if stored:
        # Always include the current effective default so a misconfigured allow-list
        # can't lock the admin out of their own model.
        default = effective_pro_model()
        if default not in stored:
            stored = [default, *stored]
        return stored
    return _default_allow_list("pro")


def allowed_flash_models() -> list[str]:
    stored = _parse_allowed_setting(SETTING_LLM_ALLOWED_FLASH)
    if stored:
        default = effective_flash_model()
        if default not in stored:
            stored = [default, *stored]
        return stored
    return _default_allow_list("flash")


def set_allowed_pro_models(models: list[str]) -> None:
    cleaned = [m for m in models if is_known_model(m) and supports_role(m, "pro")]
    set_setting(SETTING_LLM_ALLOWED_PRO, json.dumps(cleaned))


def set_allowed_flash_models(models: list[str]) -> None:
    cleaned = [m for m in models if is_known_model(m) and supports_role(m, "flash")]
    set_setting(SETTING_LLM_ALLOWED_FLASH, json.dumps(cleaned))


# ─── Per-request user overrides ──────────────────────────────────────────────


class RouterModelNotDrawable(ValueError):
    """Raised when a user submits a `flash`-only model for the pro slot."""

    def __init__(self, model_id: str, label: str):
        super().__init__(model_id)
        self.model_id = model_id
        self.label = label


class ModelNotAllowed(ValueError):
    """Raised when a user submits a model that admin hasn't allow-listed."""

    def __init__(self, model_id: str):
        super().__init__(model_id)
        self.model_id = model_id


def resolve_pro_model(requested: str | None) -> str:
    """Validate user-supplied pro model against (a) catalog, (b) role, (c) allow-list.

    Returns the resolved model id (defaults to effective_pro_model() when None).
    Raises:
      - RouterModelNotDrawable if the model exists but is flash-only
      - ModelNotAllowed if the model is not in the admin allow-list
    """
    if not requested:
        return effective_pro_model()
    spec: ModelSpec | None = get_model(requested)
    if spec is None:
        raise ModelNotAllowed(requested)
    if spec.role == "flash":
        raise RouterModelNotDrawable(requested, spec.label)
    if requested not in allowed_pro_models():
        raise ModelNotAllowed(requested)
    return requested
