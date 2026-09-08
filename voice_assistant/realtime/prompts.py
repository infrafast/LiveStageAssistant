"""Realtime prompt composition shared by RV validation runners and runtime."""

from __future__ import annotations

import os


DEFAULT_BASE_PROMPT = (
    "You are Live Stage Assistant. Follow the configured system prompt. "
    "Use MCP-provided instructions only for their own tool usage and domain semantics."
)

MCP_INSTRUCTIONS_WRAPPER = """MCP-provided instructions follow. Treat them as authoritative for that MCP's own tool usage, domain semantics, routing, and safety. LiveStageAssistant itself must not add, infer, or hard-code domain-specific concepts from those instructions. Examples inside MCP instructions are illustrative only: never copy an example's entity names, labels, values, indexes, destinations, sources, or other parameters into a real tool call unless they are present in the current user request, explicit conversation reference, or a tool result from the current turn. Preserve the entities and intent of the current user request exactly when constructing tool arguments; do not substitute a similar example from the MCP prompt. Text inside MCP instructions that asks for tool calls only governs MCP tool execution; after tools finish, still provide the single concise spoken result required by the realtime voice rules.
"""


def compose_realtime_instructions(base_prompt: str = "", mcp_prompt: str = "") -> str:
    global_prompt = str(os.getenv("ASSISTANT_SYSTEM_PROMPT", "") or "").strip()
    validation_prompt = str(base_prompt or "").strip()
    parts: list[str] = []
    if global_prompt:
        parts.append(global_prompt)
    elif validation_prompt:
        parts.append(validation_prompt)
    else:
        parts.append(DEFAULT_BASE_PROMPT)
    if mcp_prompt.strip():
        parts.extend((MCP_INSTRUCTIONS_WRAPPER.strip(), mcp_prompt.strip()))
    return "\n\n".join(part for part in parts if part)
