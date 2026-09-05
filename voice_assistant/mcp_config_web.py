"""Web-safe facade for canonical MCP realtime configuration.

This module keeps HTTP/UI code independent from the canonical storage details.
It exposes only non-secret configuration values and applies targeted updates via
``voice_assistant.realtime.mcp_config``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from .realtime.mcp_config import (
    CanonicalMCPServerConfig,
    load_mcp_inventory,
    update_mcp_realtime_policy,
)


def _tool_names(values: Sequence[Any] | None) -> list[str]:
    result: list[str] = []
    for item in values or ():
        if isinstance(item, Mapping):
            text = str(item.get("name") or "").strip()
        else:
            text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def server_web_payload(
    server: CanonicalMCPServerConfig,
    *,
    discovered_tools: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Return the editable, non-secret realtime policy for one MCP server.

    ``discovered_tools`` is runtime/UI metadata only. It is never persisted by
    this module; the canonical JSON stores only ``allowedTools`` when a server
    is configured in restricted mode.
    """
    return {
        "name": server.name,
        "native_url": server.native.url,
        "native_headers_configured": bool(server.native.headers),
        "realtime_transport": server.realtime.transport,
        "permission_mode": server.realtime.permissions.mode,
        "allowed_tools": list(server.realtime.permissions.allowed_tools),
        "discovered_tools": _tool_names(discovered_tools),
    }


def load_web_mcp_policies(
    path: str | Path,
    *,
    discovered_tools: Mapping[str, Sequence[Any]] | None = None,
) -> list[dict[str, Any]]:
    inventory = load_mcp_inventory(path)
    catalog = discovered_tools or {}
    return [
        server_web_payload(inventory[name], discovered_tools=catalog.get(name))
        for name in sorted(inventory)
    ]


def update_web_mcp_policy(
    path: str | Path,
    server_name: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate/apply one GUI policy update and return its safe representation.

    Native headers are deliberately not editable through this first RV2D GUI
    slice. Omitting them preserves any existing secret headers on disk.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("MCP realtime policy payload must be an object")

    transport = str(payload.get("realtime_transport") or "").strip().lower()
    permission_mode = str(payload.get("permission_mode") or "").strip().lower()
    native_url_raw = payload.get("native_url")
    native_url = None if native_url_raw is None else str(native_url_raw).strip()
    allowed_raw = payload.get("allowed_tools")
    if allowed_raw is None:
        allowed_tools: list[str] = []
    elif isinstance(allowed_raw, (list, tuple)):
        allowed_tools = [str(item).strip() for item in allowed_raw if str(item).strip()]
    else:
        raise ValueError("allowed_tools must be an array")

    updated = update_mcp_realtime_policy(
        path,
        server_name,
        transport=transport,
        permission_mode=permission_mode,
        allowed_tools=allowed_tools,
        native_url=native_url,
        native_headers=None,
    )
    return server_web_payload(updated)
