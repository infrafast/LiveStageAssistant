"""Realtime prompt composition using the engine-neutral LSA prompt contract."""

from __future__ import annotations

try:
    from ..prompt_contract import DEFAULT_ASSISTANT_SYSTEM_PROMPT, compose_engine_prompt
except ImportError:  # pragma: no cover - direct script fallback
    from prompt_contract import DEFAULT_ASSISTANT_SYSTEM_PROMPT, compose_engine_prompt  # type: ignore

DEFAULT_BASE_PROMPT = DEFAULT_ASSISTANT_SYSTEM_PROMPT


def compose_realtime_instructions(
    base_prompt: str = "",
    mcp_prompt: str = "",
    session_context: str = "",
    *,
    log_prefix: str = "Realtime prompt",
) -> str:
    return compose_engine_prompt(
        configured_prompt=base_prompt or None,
        mcp_prompt=mcp_prompt,
        session_context=session_context,
        require_configured_prompt=True,
        fallback_prompt=DEFAULT_ASSISTANT_SYSTEM_PROMPT,
        log_prefix=log_prefix,
    )
