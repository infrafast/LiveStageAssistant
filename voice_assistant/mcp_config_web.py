"""Web-safe facade for canonical MCP configuration.

The browser uses provider-neutral names (HTTPS / STDIO / Auto). Storage keeps
``native`` as the existing internal name for provider-reachable HTTPS MCP.
No secret header/env values are returned to the browser.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

try:
    from .realtime.mcp_config import CanonicalMCPServerConfig, load_mcp_inventory, normalize_mcp_server, update_mcp_realtime_policy
except ImportError:  # pragma: no cover - direct script fallback
    from realtime.mcp_config import CanonicalMCPServerConfig, load_mcp_inventory, normalize_mcp_server, update_mcp_realtime_policy

WEB_PERMISSION_MODES = {"open", "approval"}
WEB_TRANSPORTS = {"auto", "https", "native", "stdio"}
SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

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

def _validated_local_url(value: str | None) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Local MCP URL must be a valid http:// or https:// URL")
    return url

def _server_name(value: Any) -> str:
    name = str(value or "").strip()
    if not SERVER_NAME_RE.fullmatch(name):
        raise ValueError("MCP name must use only letters, numbers, '.', '_' or '-' (max 64 chars)")
    return name

def _read_raw(path: str | Path) -> tuple[Path, dict[str, Any]]:
    config_path = Path(path)
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"could not read MCP config {config_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in MCP config {config_path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("mcpServers"), dict):
        raise ValueError("MCP config has no mcpServers object")
    return config_path, payload

def _write_raw(path: Path, payload: Mapping[str, Any]) -> None:
    original_mode = (path.stat().st_mode & 0o777) if path.exists() else 0o644
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.chmod(temporary, original_mode)
    temporary.replace(path)

def server_web_payload(server: CanonicalMCPServerConfig) -> dict[str, Any]:
    permission_mode = server.realtime.permissions.mode if server.realtime.permissions.mode in WEB_PERMISSION_MODES else "open"
    local = server.local_entry
    args = local.get("args") if isinstance(local.get("args"), list) else []
    routing = server.assistant_options.get("routing", "") if isinstance(server.assistant_options, dict) else ""
    return {
        "name": server.name,
        "enabled": server.raw_entry.get("enabled", True) is not False,
        "command": str(local.get("command") or ""),
        "args": [str(item) for item in args],
        "local_url": str(local.get("url") or ""),
        "routing": ",".join(str(item) for item in routing) if isinstance(routing, list) else str(routing or ""),
        "https_url": server.native.url,
        "native_url": server.native.url,
        "auth_configured": bool(server.native.headers),
        "native_headers_configured": bool(server.native.headers),
        "transport": _web_transport(server.realtime.transport),
        "realtime_transport": _web_transport(server.realtime.transport),
        "permission_mode": permission_mode,
    }

def load_web_mcp_policies(path: str | Path) -> list[dict[str, Any]]:
    inventory = load_mcp_inventory(path)
    return [server_web_payload(inventory[name]) for name in sorted(inventory)]

def _validate_policy(transport: str, permission_mode: str, https_url: str | None) -> tuple[str, str, str | None]:
    storage_transport = _storage_transport(transport)
    permission = str(permission_mode or "").strip().lower()
    if permission not in WEB_PERMISSION_MODES:
        raise ValueError("permission_mode must be 'open' or 'approval'")
    if storage_transport == "stdio" and permission == "approval":
        raise ValueError("approval is not supported with explicit STDIO transport")
    if storage_transport == "native" and not https_url:
        raise ValueError("HTTPS transport requires a Provider MCP https:// URL")
    if permission == "approval" and not https_url:
        raise ValueError("approval requires a Provider MCP https:// URL")
    return storage_transport, permission, https_url

def update_web_mcp_policy(path: str | Path, server_name: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("MCP realtime policy payload must be an object")
    raw_url = payload.get("https_url") if "https_url" in payload else payload.get("native_url")
    native_url = _validated_https_url(None if raw_url is None else str(raw_url))
    storage_transport, permission_mode, native_url = _validate_policy(str(payload.get("realtime_transport") or payload.get("transport") or ""), str(payload.get("permission_mode") or ""), native_url)
    updated = update_mcp_realtime_policy(path, _server_name(server_name), transport=storage_transport, permission_mode=permission_mode, allowed_tools=[], native_url=native_url, native_headers=None)
    return server_web_payload(updated)

def save_web_mcp_server(path: str | Path, payload: Mapping[str, Any], *, existing_name: str = "") -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("MCP server payload must be an object")
    name = _server_name(payload.get("name"))
    current_name = _server_name(existing_name) if existing_name else name
    config_path, root = _read_raw(path)
    servers = root["mcpServers"]
    creating = not existing_name
    if creating and name in servers:
        raise ValueError(f"MCP server {name!r} already exists")
    if not creating and current_name not in servers:
        raise ValueError(f"MCP server {current_name!r} does not exist")
    if not creating and name != current_name and name in servers:
        raise ValueError(f"MCP server {name!r} already exists")
    entry = dict(servers.get(current_name) or {})
    entry["enabled"] = bool(payload.get("enabled", entry.get("enabled", True)))
    command = str(payload.get("command") or "").strip()
    local_url = _validated_local_url(payload.get("local_url"))
    raw_args = payload.get("args") or []
    if isinstance(raw_args, str):
        try:
            raw_args = json.loads(raw_args) if raw_args.strip() else []
        except json.JSONDecodeError as exc:
            raise ValueError("STDIO args must be a JSON array") from exc
    if not isinstance(raw_args, list) or not all(isinstance(item, (str, int, float, bool)) for item in raw_args):
        raise ValueError("STDIO args must be a JSON array of scalar values")
    args = [str(item) for item in raw_args]
    if command:
        entry["command"] = command; entry["args"] = args; entry.pop("url", None)
    elif local_url:
        entry["url"] = local_url; entry.pop("command", None); entry.pop("args", None)
    else:
        entry.pop("command", None); entry.pop("args", None); entry.pop("url", None)
    routing = str(payload.get("routing") or "").strip()
    options = entry.get("assistantOptions") if isinstance(entry.get("assistantOptions"), dict) else {}
    if routing: options["routing"] = routing
    else: options.pop("routing", None)
    if options: entry["assistantOptions"] = options
    else: entry.pop("assistantOptions", None)
    https_url = _validated_https_url(str(payload.get("https_url") or payload.get("native_url") or "")) or ""
    storage_transport, permission, _ = _validate_policy(str(payload.get("realtime_transport") or payload.get("transport") or "auto"), str(payload.get("permission_mode") or "open"), https_url)
    if not command and not local_url and not https_url:
        raise ValueError("Configure at least STDIO command, Local MCP URL, or Provider HTTPS URL")
    native = entry.get("native") if isinstance(entry.get("native"), dict) else {}
    if https_url:
        native["url"] = https_url; entry["native"] = native
    elif "native" in entry:
        native.pop("url", None)
        if native: entry["native"] = native
        else: entry.pop("native", None)
    entry["realtime"] = {"transport": storage_transport, "permissions": {"mode": permission}}
    normalize_mcp_server(name, entry)
    if current_name != name: servers.pop(current_name, None)
    servers[name] = entry; root["mcpServers"] = servers; _write_raw(config_path, root)
    return server_web_payload(load_mcp_inventory(config_path)[name])

def delete_web_mcp_server(path: str | Path, server_name: str) -> str:
    name = _server_name(server_name)
    config_path, root = _read_raw(path)
    servers = root["mcpServers"]
    if name not in servers:
        raise ValueError(f"MCP server {name!r} does not exist")
    servers.pop(name); root["mcpServers"] = servers; _write_raw(config_path, root)
    return name


def _probe_http(url: str, headers: Mapping[str, Any] | None, timeout: float) -> tuple[bool, str]:
    if not url:
        return False, "no URL configured"
    request = Request(url, method="GET", headers={str(k): str(v) for k, v in (headers or {}).items()})
    try:
        with urlopen(request, timeout=timeout) as response:
            return True, f"HTTP {getattr(response, 'status', 200)}"
    except HTTPError as exc:
        return True, f"HTTP {exc.code} (endpoint reachable)"
    except (URLError, TimeoutError, OSError) as exc:
        return False, str(exc)


def _probe_stdio(entry: Mapping[str, Any], timeout: float) -> tuple[bool, str]:
    command = str(entry.get("command") or "").strip()
    if not command:
        return False, "no STDIO command configured"
    args = entry.get("args") if isinstance(entry.get("args"), list) else []
    env = os.environ.copy()
    raw_env = entry.get("env") if isinstance(entry.get("env"), Mapping) else {}
    env.update({str(k): str(v) for k, v in raw_env.items()})
    try:
        process = subprocess.Popen([command, *[str(item) for item in args]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    except OSError as exc:
        return False, f"could not start command: {exc}"
    deadline = time.monotonic() + min(max(timeout, 0.2), 2.0)
    try:
        while time.monotonic() < deadline:
            code = process.poll()
            if code is not None:
                return (code == 0), f"process exited with code {code}"
            time.sleep(0.05)
        return True, "process started and remained alive"
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=0.5)


def test_web_mcp_server(path: str | Path, server_name: str, *, timeout: float = 2.0) -> dict[str, Any]:
    name = _server_name(server_name)
    config_path, root = _read_raw(path)
    raw = root["mcpServers"].get(name)
    if not isinstance(raw, Mapping):
        raise ValueError(f"MCP server {name!r} does not exist")
    server = normalize_mcp_server(name, raw)
    enabled = raw.get("enabled", True) is not False
    transport = server.realtime.transport
    routes: list[dict[str, Any]] = []
    command = str(server.local_entry.get("command") or "").strip()
    local_url = str(server.local_entry.get("url") or "").strip()
    if command:
        healthy, detail = _probe_stdio(server.local_entry, timeout)
        routes.append({"transport": "stdio", "label": "STDIO", "configured": True, "healthy": healthy, "detail": detail})
    if local_url:
        headers = server.local_entry.get("headers") if isinstance(server.local_entry.get("headers"), Mapping) else None
        healthy, detail = _probe_http(local_url, headers, timeout)
        routes.append({"transport": "local_url", "label": "Local MCP URL", "configured": True, "healthy": healthy, "detail": detail})
    if server.native.url:
        healthy, detail = _probe_http(server.native.url, server.native.headers, timeout)
        routes.append({"transport": "https", "label": "Provider HTTPS URL", "configured": True, "healthy": healthy, "detail": detail})

    healthy_count = sum(1 for route in routes if route.get("healthy") is True)
    healthy = bool(routes) and healthy_count == len(routes)
    status = "healthy" if healthy else "partial" if healthy_count else "unavailable"
    tested = ",".join(str(route["transport"]) for route in routes)
    detail = (
        "; ".join(f"{route['label']}: {route['detail']}" for route in routes)
        if routes
        else "no testable route configured"
    )
    return {
        "ok": True,
        "server": name,
        "enabled": enabled,
        "configured_transport": _web_transport(transport),
        "tested_transport": tested,
        "healthy": healthy,
        "status": status,
        "routes": routes,
        "detail": detail,
        "auth_configured": bool(server.native.headers),
        "config_path": str(config_path),
    }
