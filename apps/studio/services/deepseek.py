"""Back-compat shim. The chat orchestration moved to `llm.py` when the
multi-provider abstraction landed; this module re-exports the public surface
so existing imports keep working without a rename sweep."""

from .llm import (  # noqa: F401
    CHAT_MAX_TOKENS,
    ROUTER_MAX_TOKENS,
    call_deepseek,
    call_llm,
)
