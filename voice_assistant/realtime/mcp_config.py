"""Canonical, backward-compatible MCP configuration normalization for realtime.

RV2D keeps existing MCP JSON files usable while adding optional provider-native
and realtime policy blocks. Legacy local execution fields remain authoritative
for the existing LSA MCP client.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Mapping


VALID_TRANSPORTS = {"native", "stdio", "auto"}
VALID_PERMISSION_MODES = {"open", "approval", "restricted"}


@dataclass(frozen=True)
class MCPPermissionPolicy:
    mode: str = "open"
    allowed_tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class MCPNativeConfig:
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MCPRealtimePolicy:
    transport: str = "stdio"
    permissions: MCPPermissionPolicy = field(default_factory=MCPPermissionPolicy)


@dataclass(frozen=True)
class CanonicalMCPServerConfig:
    name: str
    local_entry: dict[str, Any]
    native: MCPNativeConfig
    realtime: MCPRealtimePolicy
    assistant_options: dict[str, Any] = field(default_factory=dict)
    raw_entry: dict[str, Any] = field(default_factory=dict)


def _mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return {str(key): item for key, item in value.items()}


def _string_mapping(value: Any, *, field_name: str) -> dict[str, str]:
    mapping = _mapping(value, field_name=field_name)
    return {str(key): str(item) for key, item in mapping.items()}


def _allowed_tools(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("realtime.permissions.allowedTools must be an array")
    result: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return tuple(result)


def normalize_mcp_server(name: str, entry: Mapping[str, Any]) -> CanonicalMCPServerConfig:
    raw = _mapping(entry, field_name=f"mcpServers.{name}")
    native_block = _mapping(raw.get("native"), field_name=f"mcpServers.{name}.native")
    realtime_block = _mapping(raw.get("realtime"), field_name=f"mcpServers.{name}.realtime")

    configured_native_url = str(native_block.get("url") or "").strip()
    legacy_url = str(raw.get("url") or "").strip()
    native_url = configured_native_url or (legacy_url if legacy_url.lower().startswith("https://") else "")

    native_headers_source = native_block.get("headers")
    if native_headers_source is None and native_url and native_url == legacy_url:
        native_headers_source = raw.get("headers")
    native_headers = _string_mapping(native_headers_source, field_name=f"mcpServers.{name}.native.headers")

    transport = str(realtime_block.get("transport") or "stdio").strip().lower()
    if transport not in VALID_TRANSPORTS:
        raise ValueError(
            f"mcpServers.{name}.realtime.transport must be one of {sorted(VALID_TRANSPORTS)}, got {transport!r}"
        )

    permissions_block = realtime_block.get("permissions")
    if permissions_block is None:
        legacy_permission = realtime_block.get("permission")
        permissions = {"mode": legacy_permission} if legacy_permission else {}
    elif isinstance(permissions_block, str):
        permissions = {"mode": permissions_block}
    else:
        permissions = _mapping(
            permissions_block,
            field_name=f"mcpServers.{name}.realtime.permissions",
        )

    permission_mode = str(permissions.get("mode") or "open").strip().lower()
    if permission_mode not in VALID_PERMISSION_MODES:
        raise ValueError(
            f"mcpServers.{name}.realtime.permissions.mode must be one of {sorted(VALID_PERMISSION_MODES)}, got {permission_mode!r}"
        )
    allowed_tools = _allowed_tools(permissions.get("allowedTools"))
    if permission_mode == "restricted" and not allowed_tools:
        raise ValueError(f"mcpServers.{name}: restricted permission mode requires allowedTools")

    local_entry = dict(raw)
    local_entry.pop("native", None)
    local_entry.pop("realtime", None)

    assistant_options = _mapping(raw.get("assistantOptions"), field_name=f"mcpServers.{name}.assistantOptions")
    return CanonicalMCPServerConfig(
        name=name,
        local_entry=local_entry,
        native=MCPNativeConfig(url=native_url, headers=native_headers),
        realtime=MCPRealtimePolicy(
            transport=transport,
            permissions=MCPPermissionPolicy(mode=permission_mode, allowed_tools=allowed_tools),
        ),
        assistant_options=assistant_options,
        raw_entry=dict(raw),
    )


def normalize_mcp_inventory(payload: Mapping[str, Any]) -> dict[str, CanonicalMCPServerConfig]:
    root = _mapping(payload, field_name="MCP config")
    servers = _mapping(root.get("mcpServers"), field_name="mcpServers")
    return {name: normalize_mcp_server(name, entry) for name, entry in servers.items()}


def load_mcp_inventory(path: str | Path) -> dict[str, CanonicalMCPServerConfig]:
    config_path = Path(path)
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"could not read MCP config {config_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in MCP config {config_path}: {exc}") from exc
    return normalize_mcp_inventory(payload)


def server_summary(server: CanonicalMCPServerConfig) -> dict[str, Any]:
    """Return a safe diagnostic summary without environment values or auth headers."""
    local_transport = "stdio" if server.local_entry.get("command") else ("http" if server.local_entry.get("url") else "none")
    return {
        "name": server.name,
        "localTransport": local_transport,
        "hasNativeUrl": bool(server.native.url),
        "nativeUrlScheme": "https" if server.native.url.lower().startswith("https://") else "",
        "realtimeTransport": server.realtime.transport,
        "permissionMode": server.realtime.permissions.mode,
        "allowedToolCount": len(server.realtime.permissions.allowed_tools),
        "hasAssistantOptions": bool(server.assistant_options),
    }
