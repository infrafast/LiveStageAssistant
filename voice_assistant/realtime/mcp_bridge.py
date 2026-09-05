"""Generic Realtime -> existing LSA MCP client bridge.

The bridge deliberately contains no mixer/lighting/domain logic. It reuses the
same mcp-use MCPClient/session primitives as the classic assistant and exposes
selected MCP tools to realtime providers as ordinary function tools.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

from mcp_use import MCPClient

from .engine import RealtimeFunctionTool


@dataclass(frozen=True)
class BridgeToolTarget:
    server: str
    tool: str


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "model_dump"):
        try:
            return _jsonable(value.model_dump())
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            return _jsonable(value.dict())
        except Exception:
            pass
    result: dict[str, Any] = {}
    for name in ("type", "text", "data", "mimeType", "uri", "name", "description"):
        if hasattr(value, name):
            result[name] = _jsonable(getattr(value, name))
    return result or str(value)


def _schema_from_tool(tool: Any) -> dict[str, Any]:
    schema = getattr(tool, "inputSchema", None)
    if schema is None:
        schema = getattr(tool, "input_schema", None)
    if hasattr(schema, "model_dump"):
        schema = schema.model_dump()
    if not isinstance(schema, dict):
        schema = {"type": "object", "properties": {}}
    schema = dict(schema)
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    return schema


def _function_name(server: str, tool: str, used: set[str]) -> str:
    raw = f"mcp__{server}__{tool}"
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", raw)
    if len(safe) > 60:
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
        safe = f"{safe[:51]}_{digest}"
    candidate = safe
    suffix = 2
    while candidate in used:
        tail = f"_{suffix}"
        candidate = f"{safe[:60-len(tail)]}{tail}"
        suffix += 1
    used.add(candidate)
    return candidate


class RealtimeMCPBridge:
    """Bridge realtime function calls to existing mcp-use MCP sessions."""

    def __init__(
        self,
        config: dict[str, Any],
        *,
        server_names: tuple[str, ...] | list[str] | None = None,
        allowed_tools: dict[str, set[str] | tuple[str, ...] | list[str]] | None = None,
        client: MCPClient | None = None,
    ) -> None:
        servers = config.get("mcpServers") or {}
        if not isinstance(servers, dict):
            raise ValueError("MCP config has no mcpServers object")
        selected = tuple(server_names or tuple(servers.keys()))
        unknown = [name for name in selected if name not in servers]
        if unknown:
            raise ValueError(f"unknown MCP bridge server(s): {', '.join(unknown)}")
        self.config = dict(config)
        self.server_names = selected
        self.allowed_tools = {
            str(server): {str(tool) for tool in tools}
            for server, tools in (allowed_tools or {}).items()
        }
        self.client = client or MCPClient.from_dict(self._config_subset())
        self._owns_client = client is None
        self._tool_targets: dict[str, BridgeToolTarget] = {}
        self._function_tools: tuple[RealtimeFunctionTool, ...] = ()

    def _config_subset(self) -> dict[str, Any]:
        servers = self.config.get("mcpServers") or {}
        subset = dict(self.config)
        subset["mcpServers"] = {name: servers[name] for name in self.server_names}
        return subset

    @property
    def function_tools(self) -> tuple[RealtimeFunctionTool, ...]:
        return self._function_tools

    @property
    def tool_targets(self) -> dict[str, BridgeToolTarget]:
        return dict(self._tool_targets)

    async def start(self) -> tuple[RealtimeFunctionTool, ...]:
        used_names: set[str] = set()
        targets: dict[str, BridgeToolTarget] = {}
        functions: list[RealtimeFunctionTool] = []
        for server_name in self.server_names:
            if server_name not in getattr(self.client, "sessions", {}):
                await self.client.create_session(server_name)
            session = self.client.get_session(server_name)
            tools = await session.list_tools()
            allowed = self.allowed_tools.get(server_name)
            for tool in tools or []:
                tool_name = str(getattr(tool, "name", "") or "").strip()
                if not tool_name or (allowed is not None and tool_name not in allowed):
                    continue
                exposed_name = _function_name(server_name, tool_name, used_names)
                description = str(getattr(tool, "description", "") or "").strip()
                functions.append(
                    RealtimeFunctionTool(
                        name=exposed_name,
                        description=(
                            f"MCP server {server_name}: {description}"
                            if description
                            else f"MCP tool {tool_name} on server {server_name}."
                        ),
                        parameters=_schema_from_tool(tool),
                    )
                )
                targets[exposed_name] = BridgeToolTarget(server=server_name, tool=tool_name)
        self._tool_targets = targets
        self._function_tools = tuple(functions)
        return self._function_tools

    async def execute(self, exposed_name: str, arguments: str | dict[str, Any] | None) -> dict[str, Any]:
        target = self._tool_targets.get(exposed_name)
        if target is None:
            raise ValueError(f"unknown realtime bridge tool: {exposed_name}")
        if isinstance(arguments, str):
            try:
                parsed = json.loads(arguments or "{}")
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON arguments for {exposed_name}: {exc}") from exc
        elif isinstance(arguments, dict):
            parsed = dict(arguments)
        elif arguments is None:
            parsed = {}
        else:
            raise ValueError(f"unsupported arguments type for {exposed_name}")
        if not isinstance(parsed, dict):
            raise ValueError(f"tool arguments for {exposed_name} must decode to an object")

        session = self.client.get_session(target.server)
        result = await session.call_tool(target.tool, parsed)
        return {
            "transport": "stdio/bridge",
            "server": target.server,
            "tool": target.tool,
            "is_error": bool(getattr(result, "isError", False)),
            "content": _jsonable(getattr(result, "content", [])),
            "structured_content": _jsonable(getattr(result, "structuredContent", None)),
        }

    async def close(self) -> None:
        if self._owns_client and getattr(self.client, "sessions", None):
            await self.client.close_all_sessions()
