"""Small backend helpers for MCP web endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

try:
    from .mcp_config_web import delete_web_mcp_server, save_web_mcp_server, update_web_mcp_policy
except ImportError:  # pragma: no cover - direct script fallback
    from mcp_config_web import delete_web_mcp_server, save_web_mcp_server, update_web_mcp_policy


def mcp_config_path_from_snapshot(snapshot: Mapping[str, Any]) -> Path:
    """Resolve the active MCP_CONFIG exactly like the runtime backend does."""
    config = snapshot.get("config") or {}
    if not isinstance(config, Mapping):
        raise ValueError("Web snapshot config is not available")
    env_values = config.get("env") or {}
    if not isinstance(env_values, Mapping):
        raise ValueError("Web snapshot env config is not available")
    raw_path = str(env_values.get("MCP_CONFIG") or "").strip()
    if not raw_path:
        raise ValueError("MCP_CONFIG is not set in the active env file")
    path = Path(raw_path)
    if path.is_absolute():
        return path
    env_file = str(snapshot.get("env_file") or "").strip()
    candidates = []
    if env_file:
        candidates.append(Path(env_file).expanduser().parent / path)
    candidates.append(Path.cwd() / path)
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[0])


def _refreshed(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"could not read MCP config {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in MCP config {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"MCP config {path} must contain a JSON object")
    return payload


def save_mcp_realtime_policy_from_snapshot(snapshot: Mapping[str, Any], server_name: str, policy: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    name = str(server_name or "").strip()
    if not name:
        raise ValueError("server is required")
    if not isinstance(policy, Mapping):
        raise ValueError("policy must be an object")
    path = mcp_config_path_from_snapshot(snapshot)
    safe_policy = update_web_mcp_policy(path, name, policy)
    return safe_policy, _refreshed(path)


def save_mcp_server_from_snapshot(snapshot: Mapping[str, Any], payload: Mapping[str, Any], *, existing_name: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
    path = mcp_config_path_from_snapshot(snapshot)
    safe_server = save_web_mcp_server(path, payload, existing_name=existing_name)
    return safe_server, _refreshed(path)


def delete_mcp_server_from_snapshot(snapshot: Mapping[str, Any], server_name: str) -> tuple[str, dict[str, Any]]:
    path = mcp_config_path_from_snapshot(snapshot)
    deleted = delete_web_mcp_server(path, server_name)
    return deleted, _refreshed(path)
