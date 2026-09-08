"""Realtime prompt composition using the engine-neutral LSA prompt contract."""

from __future__ import annotations

try:
    from ..prompt_contract import (
        DEFAULT_ASSISTANT_SYSTEM_PROMPT,
        MCP_INSTRUCTIONS_WRAPPER,
        PRODUCT_IDENTITY_PROMPT,
        compose_engine_prompt,
    )
except ImportError:  # pragma: no cover - direct script fallback
    from prompt_contract import (  # type: ignore
        DEFAULT_ASSISTANT_SYSTEM_PROMPT,
        MCP_INSTRUCTIONS_WRAPPER,
        PRODUCT_IDENTITY_PROMPT,
        compose_engine_prompt,
    )

DEFAULT_BASE_PROMPT = DEFAULT_ASSISTANT_SYSTEM_PROMPT


def compose_realtime_instructions(base_prompt: str = "", mcp_prompt: str = "") -> str:
    return compose_engine_prompt(
        configured_prompt=None,
        mcp_prompt=mcp_prompt,
        require_configured_prompt=True,
        fallback_prompt=base_prompt or DEFAULT_ASSISTANT_SYSTEM_PROMPT,
        log_prefix="Realtime prompt",
    )
