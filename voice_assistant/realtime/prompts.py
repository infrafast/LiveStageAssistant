"""Realtime prompt composition shared by RV validation runners and runtime."""

from __future__ import annotations

import os


DEFAULT_BASE_PROMPT = (
    "You are Live Stage Assistant. Follow the configured system prompt. "
    "Use MCP-provided instructions only for their own tool usage and domain semantics."
)

MCP_INSTRUCTIONS_WRAPPER = """MCP-provided instructions follow. Treat them as authoritative for that MCP's own tool usage, domain semantics, routing, and safety. LiveStageAssistant itself must not add, infer, or hard-code domain-specific concepts from those instructions. Examples inside MCP instructions are illustrative only: never copy an example's entity names, labels, values, indexes, destinations, sources, or other parameters into a real tool call unless they are present in the current user request, explicit conversation reference, or a tool result from the current turn. Preserve the entities and intent of the current user request exactly when constructing tool arguments; do not substitute a similar example from the MCP prompt. Text inside MCP instructions that asks for tool calls only governs MCP tool execution; after tools finish, still provide the single concise spoken result required by the realtime voice rules.
"""


def _bool_env(name: str, default: bool = True) -> bool:
    value = str(os.getenv(name) if os.getenv(name) not in (None, "") else ("true" if default else "false")).strip().lower()
    return value in {"1", "true", "yes", "y", "on"}


def compose_realtime_instructions(base_prompt: str = "", mcp_prompt: str = "") -> str:
    global_prompt = str(os.getenv("ASSISTANT_SYSTEM_PROMPT", "") or "").strip()
    validation_prompt = str(base_prompt or "").strip()
    prompt_required = _bool_env("REQUIRE_ASSISTANT_SYSTEM_PROMPT", True)
    parts: list[str] = []
    if global_prompt:
        print(f"Realtime prompt: ASSISTANT_SYSTEM_PROMPT loaded chars={len(global_prompt)}", flush=True)
        parts.append(global_prompt)
    elif prompt_required:
        raise RuntimeError("ASSISTANT_SYSTEM_PROMPT is required for the configured runtime profile")
    elif validation_prompt:
        print("Realtime prompt: ASSISTANT_SYSTEM_PROMPT missing; using validation base prompt", flush=True)
        parts.append(validation_prompt)
    else:
        print("Realtime prompt: ASSISTANT_SYSTEM_PROMPT missing; using default base prompt", flush=True)
        parts.append(DEFAULT_BASE_PROMPT)

    mcp_enabled = _bool_env("MCP_LOAD_SERVER_PROMPT", True)
    mcp_text = str(mcp_prompt or "").strip()
    if not mcp_enabled:
        print("Realtime prompt: MCP prompts disabled by MCP_LOAD_SERVER_PROMPT", flush=True)
    elif mcp_text:
        print(f"Realtime prompt: MCP prompts loaded chars={len(mcp_text)}", flush=True)
        parts.extend((MCP_INSTRUCTIONS_WRAPPER.strip(), mcp_text))
    else:
        print("Realtime prompt: MCP prompts enabled, none loaded", flush=True)
    return "\n\n".join(part for part in parts if part)
