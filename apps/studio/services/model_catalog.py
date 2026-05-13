"""Model catalog — DB-backed since Phase 4.

Admin manages the catalog via /admin/art/models (CRUD + toggles); the worker
reads from `llm_models` table with a 60s in-process cache so per-request
lookup stays cheap. Falls back to the hardcoded LLM_MODEL_CATALOG tuple
below if the DB is empty (fresh install before migration 0011 seeds it).

Each model has:
  - id (= db slug)   stable internal identifier used in DB, API, settings
  - label            human-facing display string
  - provider         which backend client handles the call
  - remote_id        the slug the upstream API actually expects
  - role             "flash" | "pro" | "both"
  - prompt_cents_per_mtok / completion_cents_per_mtok — for cost attribution
  - description, badges (speed/quality/cost), enabled, allowed_for_users,
    is_default_pro, is_default_flash, deprecated, beta, max_tokens_override —
    DB-only, exposed via get_model_full().

Adding a new model now:
  1. Admin opens /admin/dashboard/art/models → "Add custom model" → fills form.
  2. No code change, no deploy. Cache refreshes within 60s.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Literal

log = logging.getLogger("x106.studio.model_catalog")

Provider = Literal["deepseek", "opencode_openai", "opencode_anthropic"]
Role = Literal["flash", "pro", "both"]


@dataclass(frozen=True)
class ModelSpec:
    """Lean runtime view of one LLM model. Subset of LLMModel — only the fields
    the call path needs. Admin/API surfaces use get_model_full() for the
    metadata-rich shape (badges, deprecation, description, etc.)."""

    id: str
    label: str
    provider: Provider
    remote_id: str
    role: Role
    prompt_cents_per_mtok: int = 0
    completion_cents_per_mtok: int = 0


# ─── Hardcoded fallback ─────────────────────────────────────────────────────
# Boot-safe: if migrations haven't seeded llm_models yet (or the table query
# fails for any reason), the worker uses these. Migration 0011 mirrors this
# list so steady-state DB → identical runtime behavior.

LLM_MODEL_CATALOG: tuple[ModelSpec, ...] = (
    ModelSpec("deepseek-v4-flash", "DeepSeek V4 Flash", "deepseek", "deepseek-v4-flash", "flash",
              prompt_cents_per_mtok=14, completion_cents_per_mtok=28),
    ModelSpec("deepseek-v4-pro", "DeepSeek V4 Pro", "deepseek", "deepseek-v4-pro", "pro",
              prompt_cents_per_mtok=55, completion_cents_per_mtok=219),
    ModelSpec("opencode-go/glm-5", "GLM-5 (OpenCode)", "opencode_openai", "glm-5", "pro"),
    ModelSpec("opencode-go/glm-5.1", "GLM-5.1 (OpenCode)", "opencode_openai", "glm-5.1", "pro"),
    ModelSpec("opencode-go/kimi-k2.5", "Kimi K2.5 (OpenCode)", "opencode_openai", "kimi-k2.5", "pro"),
    ModelSpec("opencode-go/kimi-k2.6", "Kimi K2.6 (OpenCode)", "opencode_openai", "kimi-k2.6", "pro"),
    ModelSpec("opencode-go/deepseek-v4-flash", "DeepSeek V4 Flash (OpenCode)", "opencode_openai", "deepseek-v4-flash", "flash"),
    ModelSpec("opencode-go/deepseek-v4-pro", "DeepSeek V4 Pro (OpenCode)", "opencode_openai", "deepseek-v4-pro", "pro"),
    ModelSpec("opencode-go/qwen3.5-plus", "Qwen3.5 Plus (OpenCode)", "opencode_openai", "qwen3.5-plus", "both"),
    ModelSpec("opencode-go/qwen3.6-plus", "Qwen3.6 Plus (OpenCode)", "opencode_openai", "qwen3.6-plus", "pro"),
    ModelSpec("opencode-go/mimo-v2-omni", "MiMo V2 Omni (OpenCode)", "opencode_openai", "mimo-v2-omni", "flash"),
    ModelSpec("opencode-go/mimo-v2-pro", "MiMo V2 Pro (OpenCode)", "opencode_openai", "mimo-v2-pro", "pro"),
    ModelSpec("opencode-go/mimo-v2.5", "MiMo V2.5 (OpenCode)", "opencode_openai", "mimo-v2.5", "flash"),
    ModelSpec("opencode-go/mimo-v2.5-pro", "MiMo V2.5 Pro (OpenCode)", "opencode_openai", "mimo-v2.5-pro", "pro"),
    ModelSpec("opencode-go/hy3-preview", "HY3 Preview (OpenCode)", "opencode_openai", "hy3-preview", "pro"),
    ModelSpec("opencode-go/minimax-m2.5", "MiniMax M2.5 (OpenCode)", "opencode_anthropic", "minimax-m2.5", "pro"),
    ModelSpec("opencode-go/minimax-m2.7", "MiniMax M2.7 (OpenCode)", "opencode_anthropic", "minimax-m2.7", "pro"),
)


# ─── DB-backed cache ────────────────────────────────────────────────────────

_CACHE_TTL_SECONDS = 60.0
# (loaded_at_monotonic, tuple_of_specs, dict_by_id, full_rows_by_id)
_cache: tuple[float, tuple[ModelSpec, ...], dict[str, ModelSpec], dict[str, dict]] | None = None


def _load_from_db() -> tuple[tuple[ModelSpec, ...], dict[str, ModelSpec], dict[str, dict]]:
    """Query llm_models and project to (specs, by_id, full_rows_by_id).
    Returns empty tuples when the table is empty or unreachable — caller
    decides whether to fall back to LLM_MODEL_CATALOG."""
    # Local import to avoid Django app-registry boot ordering issues.
    from apps.studio.models import LLMModel

    try:
        rows = list(
            LLMModel.objects
            .order_by("sort_order", "display_name")
            .values(
                "slug", "display_name", "provider", "remote_id", "role",
                "description", "speed_badge", "quality_badge", "cost_badge",
                "enabled", "is_default_pro", "is_default_flash",
                "allowed_for_users", "deprecated", "beta",
                "prompt_cents_per_mtok", "completion_cents_per_mtok",
                "max_tokens_override",
            )
        )
    except Exception as exc:  # noqa: BLE001 — table may not exist yet on fresh boot
        log.warning("llm_models query failed: %s — using fallback catalog", exc)
        return ((), {}, {})

    specs: list[ModelSpec] = []
    full: dict[str, dict] = {}
    for r in rows:
        slug = r["slug"]
        specs.append(
            ModelSpec(
                id=slug,
                label=r["display_name"],
                provider=r["provider"],
                remote_id=r["remote_id"],
                role=r["role"],
                prompt_cents_per_mtok=int(r["prompt_cents_per_mtok"] or 0),
                completion_cents_per_mtok=int(r["completion_cents_per_mtok"] or 0),
            )
        )
        full[slug] = r
    specs_tuple = tuple(specs)
    by_id = {s.id: s for s in specs_tuple}
    return (specs_tuple, by_id, full)


def _get_cache():
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < _CACHE_TTL_SECONDS:
        return _cache

    specs, by_id, full = _load_from_db()
    if not specs:
        # DB empty (pre-seed) or unreachable — fall through to hardcoded.
        by_id = {m.id: m for m in LLM_MODEL_CATALOG}
        full = {
            slug: {
                "slug": m.id,
                "display_name": m.label,
                "provider": m.provider,
                "remote_id": m.remote_id,
                "role": m.role,
                "description": "",
                "speed_badge": "",
                "quality_badge": "",
                "cost_badge": "",
                "enabled": True,
                "is_default_pro": (m.id == "deepseek-v4-pro"),
                "is_default_flash": (m.id == "deepseek-v4-flash"),
                "allowed_for_users": (m.provider == "deepseek"),
                "deprecated": False,
                "beta": False,
                "prompt_cents_per_mtok": m.prompt_cents_per_mtok or None,
                "completion_cents_per_mtok": m.completion_cents_per_mtok or None,
                "max_tokens_override": None,
            }
            for slug, m in by_id.items()
        }
        specs = LLM_MODEL_CATALOG

    _cache = (now, specs, by_id, full)
    return _cache


def clear_cache() -> None:
    """Test/admin helper — purge cache so the next call re-reads from DB.
    Phase 4.2 admin endpoints call this after mutating a row so the change
    surfaces on the worker pod immediately rather than within 60s."""
    global _cache
    _cache = None


def all_models() -> tuple[ModelSpec, ...]:
    return _get_cache()[1]


def get_model(model_id: str) -> ModelSpec | None:
    return _get_cache()[2].get(model_id)


def get_model_full(model_id: str) -> dict | None:
    """Return the rich row (with badges + flags). Used by admin endpoints and
    by the user-facing /studio/llm/models v2 response. None when unknown."""
    return _get_cache()[3].get(model_id)


def all_models_full() -> list[dict]:
    """All rows with metadata, ordered by sort_order. Admin list endpoint."""
    cache = _get_cache()
    return [cache[3][s.id] for s in cache[1] if s.id in cache[3]]


def is_known_model(model_id: str) -> bool:
    return model_id in _get_cache()[2]


def supports_role(model_id: str, role: Role) -> bool:
    spec = get_model(model_id)
    if spec is None:
        return False
    if spec.role == "both":
        return True
    return spec.role == role


def models_for_role(role: Role) -> tuple[ModelSpec, ...]:
    return tuple(m for m in all_models() if m.role in (role, "both"))


def compute_cost_cents(
    model_id: str, prompt_tokens: int, completion_tokens: int
) -> int | None:
    """Convert token counts to cents. Returns None when the model has no rate
    (both rates 0) so the caller can store NULL instead of a misleading 0."""
    spec = get_model(model_id)
    if spec is None:
        return None
    if spec.prompt_cents_per_mtok == 0 and spec.completion_cents_per_mtok == 0:
        return None
    units = (
        prompt_tokens * spec.prompt_cents_per_mtok
        + completion_tokens * spec.completion_cents_per_mtok
    )
    return units // 1_000_000


# ─── Default resolvers (Phase 4) ────────────────────────────────────────────


def get_default_pro_slug() -> str | None:
    for row in _get_cache()[3].values():
        if row["is_default_pro"] and row["enabled"]:
            return row["slug"]
    return None


def get_default_flash_slug() -> str | None:
    for row in _get_cache()[3].values():
        if row["is_default_flash"] and row["enabled"]:
            return row["slug"]
    return None


def user_picker_models() -> list[dict]:
    """Models that should appear in the chat picker: enabled + allowed_for_users
    + NOT deprecated + role in (pro, both). Flash-only models never surface
    here — defense-in-depth on top of the picker hiding them client-side."""
    return [
        row
        for row in all_models_full()
        if row["enabled"]
        and row["allowed_for_users"]
        and not row["deprecated"]
        and row["role"] in ("pro", "both")
    ]
