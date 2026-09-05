"""Small backend helper for the RV2D MCP realtime-policy web endpoint."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .mcp_config_web import update_web_mcp_policy


def mcp_config_path_from_snapshot(snapshot: Mapping[str, Any]) -> Path:
    """Resolve the active MCP_CONFIG exactly like the classic backend does."""
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
    return path if path.is_absolute() else Path.cwd() / path


def save_mcp_realtime_policy_from_snapshot(
    snapshot: Mapping[str, Any],
    server_name: str,
    policy: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Persist one policy and return (safe policy payload, refreshed raw MCP JSON)."""
    name = str(server_name or "").strip()
    if not name:
        raise ValueError("server is required")
    if not isinstance(policy, Mapping):
        raise ValueError("policy must be an object")

    path = mcp_config_path_from_snapshot(snapshot)
    safe_policy = update_web_mcp_policy(path, name, policy)
    try:
        refreshed = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"could not read MCP config {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in MCP config {path}: {exc}") from exc
    if not isinstance(refreshed, dict):
        raise ValueError(f"MCP config {path} must contain a JSON object")
    return safe_policy, refreshed
