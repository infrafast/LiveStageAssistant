"""Web-safe facade for canonical MCP realtime configuration.

The browser uses provider-neutral names (HTTPS / STDIO / Auto). Storage keeps
``native`` as the existing internal name for provider-reachable HTTPS MCP.
No secrets are returned to the browser.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

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
WEB_TRANSPORTS = {"auto", "https", "native", "stdio"}


def _web_transport(storage_transport: str) -> str:
    return "https" if str(storage_transport).strip().lower() == "native" else str(storage_transport).strip().lower()


def _storage_transport(web_transport: str) -> str:
    normalized = str(web_transport or "").strip().lower()
    if normalized not in WEB_TRANSPORTS:
        raise ValueError("realtime_transport must be 'auto', 'https', or 'stdio'")
    return "native" if normalized in {"https", "native"} else normalized


def _validated_https_url(value: str | None) -> str | None:
    if value is None:
        return None
    url = str(value).strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise ValueError("Provider MCP URL must be a valid https:// URL")
    return url


def server_web_payload(server: CanonicalMCPServerConfig) -> dict[str, Any]:
    """Return editable, non-secret MCP policy for one logical server."""
    permission_mode = server.realtime.permissions.mode
    if permission_mode not in WEB_PERMISSION_MODES:
        permission_mode = "open"
    return {
        "name": server.name,
        "https_url": server.native.url,
        "native_url": server.native.url,  # compatibility for the current stable page
        "auth_configured": bool(server.native.headers),
        "native_headers_configured": bool(server.native.headers),
        "transport": _web_transport(server.realtime.transport),
        "realtime_transport": _web_transport(server.realtime.transport),
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
    """Validate and atomically apply one browser MCP policy update."""
    if not isinstance(payload, Mapping):
        raise ValueError("MCP realtime policy payload must be an object")

    storage_transport = _storage_transport(str(payload.get("realtime_transport") or payload.get("transport") or ""))
    permission_mode = str(payload.get("permission_mode") or "").strip().lower()
    if permission_mode not in WEB_PERMISSION_MODES:
        raise ValueError("permission_mode must be 'open' or 'approval'")
    if storage_transport == "stdio" and permission_mode == "approval":
        raise ValueError("approval is not supported with explicit STDIO transport")

    raw_url = payload.get("https_url") if "https_url" in payload else payload.get("native_url")
    native_url = _validated_https_url(None if raw_url is None else str(raw_url))
    if storage_transport == "native" and not native_url:
        raise ValueError("HTTPS transport requires a Provider MCP https:// URL")

    updated = update_mcp_realtime_policy(
        path,
        server_name,
        transport=storage_transport,
        permission_mode=permission_mode,
        allowed_tools=[],
        native_url=native_url,
        native_headers=None,
    )
    return server_web_payload(updated)
