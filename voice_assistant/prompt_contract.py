"""Engine-neutral prompt contract for all LiveStageAssistant engines.

The reference behavior is the existing main branch contract used by the
classic MCP agent: the configured ASSISTANT_SYSTEM_PROMPT is the product
identity and base system prompt, MCP prompts are appended only when enabled
by env, and session context is added as internal continuity material without
becoming a user message.
"""

from __future__ import annotations

import os
from typing import Iterable


DEFAULT_ASSISTANT_SYSTEM_PROMPT = (
    "You are Live Stage Assistant, a helpful voice assistant with access to MCP tools for live stage devices. "
    "Be precise, conservative, tool-driven, concise, and suitable for spoken output. "
    "Reply in French by default. Reply in English only when the user's latest request is clearly in English; "
    "for terse, mixed, ambiguous, or domain commands such as 'qlc rouge', answer in French. "
    "Use plain text only. Do not use emojis, emoticons, markdown, bullets, symbols, or decorative characters. "
    "Use only the MCP tools and capabilities that are actually available. Do not invent tools, OSC paths, "
    "widgets, scenes, device names, channel indexes, mappings, or unavailable features. "
    "When a tool is needed, call it silently, wait for the result, then speak exactly once with the concise verified result."
)

PRODUCT_IDENTITY_PROMPT = DEFAULT_ASSISTANT_SYSTEM_PROMPT

MCP_INSTRUCTIONS_WRAPPER = """MCP-provided instructions follow. Treat them as authoritative for that MCP's own tool usage, domain semantics, routing, and safety. LiveStageAssistant itself must not add, infer, or hard-code domain-specific concepts from those instructions. Examples inside MCP instructions are illustrative only: never copy an example's entity names, labels, values, indexes, destinations, sources, or other parameters into a real tool call unless they are present in the current user request, explicit conversation reference, or a tool result from the current turn. Preserve the entities and intent of the current user request exactly when constructing tool arguments; do not substitute a similar example from the MCP prompt. Text inside MCP instructions that asks for tool calls only governs MCP tool execution; after tools finish, still provide the single concise spoken result required by the voice rules.
"""

SESSION_CONTEXT_INSTRUCTION_HEADER = (
    "Internal active session context. Use silently for continuity, preferences, aliases and follow-up references. "
    "Never acknowledge, thank the user for, quote, summarize, or mention this context unless the user explicitly asks. "
    "Do not treat it as live external state."
)


def bool_env(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _dedupe_parts(parts: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for part in parts:
        text = str(part or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        result.append(text)
        seen.add(key)
    return result


def configured_system_prompt(*, required: bool = True, fallback: str = "") -> str:
    prompt = str(os.getenv("ASSISTANT_SYSTEM_PROMPT", "") or "").strip()
    if prompt:
        return prompt
    if required:
        raise RuntimeError("ASSISTANT_SYSTEM_PROMPT is required for the configured runtime profile")
    return str(fallback or DEFAULT_ASSISTANT_SYSTEM_PROMPT).strip()


def compose_engine_prompt(
    *,
    configured_prompt: str | None = None,
    mcp_prompt: str = "",
    session_context: str = "",
    require_configured_prompt: bool = True,
    fallback_prompt: str = "",
    log_prefix: str = "LSA prompt",
) -> str:
    """Build the effective prompt for any LSA engine.

    Shape, matching the main branch behavior:

    ASSISTANT_SYSTEM_PROMPT
    + MCP wrapper and MCP prompts when MCP_LOAD_SERVER_PROMPT enables them
    + active session context as internal prompt material

    User text must be sent separately by the engine as the user turn.
    """
    system_prompt = str(configured_prompt or "").strip()
    if not system_prompt:
        system_prompt = configured_system_prompt(
            required=require_configured_prompt,
            fallback=fallback_prompt,
        )
    if log_prefix:
        print(f"{log_prefix}: ASSISTANT_SYSTEM_PROMPT loaded chars={len(system_prompt)}", flush=True)

    parts = [system_prompt]
    mcp_enabled = bool_env("MCP_LOAD_SERVER_PROMPT", True)
    mcp_text = str(mcp_prompt or "").strip()
    if not mcp_enabled:
        if log_prefix:
            print(f"{log_prefix}: MCP prompts disabled by MCP_LOAD_SERVER_PROMPT", flush=True)
    elif mcp_text:
        if log_prefix:
            print(f"{log_prefix}: MCP prompts loaded chars={len(mcp_text)}", flush=True)
        parts.extend((MCP_INSTRUCTIONS_WRAPPER.strip(), mcp_text))
    elif log_prefix:
        print(f"{log_prefix}: MCP prompts enabled, none loaded", flush=True)

    context = str(session_context or "").strip()
    if context:
        parts.append(f"{SESSION_CONTEXT_INSTRUCTION_HEADER}\n{context}")
    return "\n\n".join(_dedupe_parts(parts))
