"""Web-safe facade for canonical MCP realtime configuration.

This module keeps HTTP/UI code independent from the canonical storage details.
It exposes only non-secret configuration values and applies targeted updates via
``voice_assistant.realtime.mcp_config``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

try:
    from .realtime.mcp_config import (
        CanonicalMCPServerConfig,
        load_mcp_inventory,
        update_mcp_realtime_policy,
    )
except ImportError:  # pragma: no cover - direct script fallback
    from realtime.mcp_config import (
        CanonicalMCPServerConfig,
        load_mcp_inventory,
        update_mcp_realtime_policy,
    )


WEB_PERMISSION_MODES = {"open", "approval"}


def server_web_payload(server: CanonicalMCPServerConfig) -> dict[str, Any]:
    """Return the editable, non-secret realtime policy for one MCP server."""
    permission_mode = server.realtime.permissions.mode
    if permission_mode not in WEB_PERMISSION_MODES:
        permission_mode = "open"
    return {
        "name": server.name,
        "native_url": server.native.url,
        "native_headers_configured": bool(server.native.headers),
        "realtime_transport": server.realtime.transport,
        "permission_mode": permission_mode,
    }


def load_web_mcp_policies(path: str | Path) -> list[dict[str, Any]]:
    inventory = load_mcp_inventory(path)
    return [server_web_payload(inventory[name]) for name in sorted(inventory)]


def update_web_mcp_policy(
    path: str | Path,
    server_name: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate/apply one GUI policy update and return its safe representation.

    The RV2D GUI intentionally exposes only ``open`` and ``approval`` permission
    modes. Native headers remain backend-only and are preserved unless changed
    by another backend path.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("MCP realtime policy payload must be an object")

    transport = str(payload.get("realtime_transport") or "").strip().lower()
    permission_mode = str(payload.get("permission_mode") or "").strip().lower()
    if permission_mode not in WEB_PERMISSION_MODES:
        raise ValueError("permission_mode must be 'open' or 'approval'")

    native_url_raw = payload.get("native_url")
    native_url = None if native_url_raw is None else str(native_url_raw).strip()

    updated = update_mcp_realtime_policy(
        path,
        server_name,
        transport=transport,
        permission_mode=permission_mode,
        allowed_tools=[],
        native_url=native_url,
        native_headers=None,
    )
    return server_web_payload(updated)
