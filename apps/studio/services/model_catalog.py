"""Single source of truth for every LLM model the studio can talk to.

Each model has:
  - id          stable internal slug used in DB, API requests, settings (e.g. "opencode-go/kimi-k2.6")
  - label       human-facing display string
  - provider    which backend client handles the call ("deepseek" | "opencode_openai" | "opencode_anthropic")
  - remote_id   the slug the upstream API actually expects (e.g. "kimi-k2.6" for OpenCode)
  - role        "flash" (intent classification only) | "pro" (scene generation) | "both"

Frontends fetch the visible catalog from /api/v1/studio/llm/models; admin
settings allow-list subsets per role. Adding a new model:
  1. Append a ModelSpec here
  2. Confirm provider can route the remote_id
  3. Restart workers (the catalog is module-level, not DB-backed)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Provider = Literal["deepseek", "opencode_openai", "opencode_anthropic"]
Role = Literal["flash", "pro", "both"]


@dataclass(frozen=True)
class ModelSpec:
    """One LLM the studio can call. Cost rates are cents per million tokens
    (0 = unknown, treated as "don't compute cost" by compute_cost_cents).
    Operator should verify rates against the provider's price sheet before
    relying on them for billing decisions — Phase 4 moves the catalog to DB
    so admin can override without a deploy."""

    id: str
    label: str
    provider: Provider
    remote_id: str
    role: Role
    prompt_cents_per_mtok: int = 0
    completion_cents_per_mtok: int = 0


# Conservative starting rates. Real DeepSeek v4 pricing is not officially
# published as of this writing; values below are working estimates calibrated
# from the v3 generation. Admin override (Phase 4) is the real source of truth.
LLM_MODEL_CATALOG: tuple[ModelSpec, ...] = (
    # DeepSeek native — what the studio shipped with
    ModelSpec("deepseek-v4-flash", "DeepSeek V4 Flash", "deepseek", "deepseek-v4-flash", "flash",
              prompt_cents_per_mtok=14, completion_cents_per_mtok=28),
    ModelSpec("deepseek-v4-pro", "DeepSeek V4 Pro", "deepseek", "deepseek-v4-pro", "pro",
              prompt_cents_per_mtok=55, completion_cents_per_mtok=219),
    # OpenCode Go OpenAI-compatible — pricing varies by upstream vendor; defaults
    # to 0 (unknown) until admin sets per-model rates via Phase 4 DB-driven catalog.
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
    # OpenCode Go Anthropic-compatible
    ModelSpec("opencode-go/minimax-m2.5", "MiniMax M2.5 (OpenCode)", "opencode_anthropic", "minimax-m2.5", "pro"),
    ModelSpec("opencode-go/minimax-m2.7", "MiniMax M2.7 (OpenCode)", "opencode_anthropic", "minimax-m2.7", "pro"),
)

_BY_ID: dict[str, ModelSpec] = {m.id: m for m in LLM_MODEL_CATALOG}


def all_models() -> tuple[ModelSpec, ...]:
    return LLM_MODEL_CATALOG


def get_model(model_id: str) -> ModelSpec | None:
    return _BY_ID.get(model_id)


def is_known_model(model_id: str) -> bool:
    return model_id in _BY_ID


def supports_role(model_id: str, role: Role) -> bool:
    spec = _BY_ID.get(model_id)
    if spec is None:
        return False
    if spec.role == "both":
        return True
    return spec.role == role


def models_for_role(role: Role) -> tuple[ModelSpec, ...]:
    return tuple(m for m in LLM_MODEL_CATALOG if m.role in (role, "both"))


def compute_cost_cents(
    model_id: str, prompt_tokens: int, completion_tokens: int
) -> int | None:
    """Convert token counts to cents. Returns None when the model has no rate
    (both rates 0) so the caller can store NULL instead of a misleading 0."""
    spec = _BY_ID.get(model_id)
    if spec is None:
        return None
    if spec.prompt_cents_per_mtok == 0 and spec.completion_cents_per_mtok == 0:
        return None
    units = (
        prompt_tokens * spec.prompt_cents_per_mtok
        + completion_tokens * spec.completion_cents_per_mtok
    )
    return units // 1_000_000
