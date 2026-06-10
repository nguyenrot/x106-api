"""Render the agent prompt from the markdown templates in ./prompts/.

Same {{variable}} substitution scheme as lattice's apps/agent/prompts.py —
templates are plain markdown so the prose can be edited without touching code.
"""

from __future__ import annotations

from pathlib import Path

from .validate import AMENITY_CATALOG, DISTRICTS, TAG_CATALOG

_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


def _load(name: str) -> str:
    return (_PROMPT_DIR / name).read_text(encoding="utf-8")


def _render(template: str, variables: dict[str, str]) -> str:
    out = template
    for key, value in variables.items():
        out = out.replace("{{" + key + "}}", value)
    return out


def build_write_review_prompt(*, today_vn: str, existing: list[tuple[str, str]]) -> str:
    """Full prompt = system.md + write-review.md with variables filled in.

    `existing` is [(name, slug)] of every review already in the DB — the
    agent's dedup context.
    """
    if existing:
        existing_block = "\n".join(f"- {name} (slug: {slug})" for name, slug in existing)
    else:
        existing_block = "(chưa có bài nào — bạn đang viết bài đầu tiên của blog)"

    task = _render(
        _load("write-review.md"),
        {
            "today_vn": today_vn,
            "districts": "\n".join(f"- {d}" for d in DISTRICTS),
            "tags_catalog": "\n".join(f"- `{slug}` — {label}" for slug, label in TAG_CATALOG.items()),
            "amenities_catalog": "\n".join(
                f"- `{slug}` — {label}" for slug, label in AMENITY_CATALOG.items()
            ),
            "existing_reviews": existing_block,
        },
    )
    return _load("system.md") + "\n\n---\n\n" + task
