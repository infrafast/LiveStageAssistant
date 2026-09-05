"""Generic Realtime -> existing LSA MCP client bridge.

The bridge deliberately contains no mixer/lighting/domain logic. It reuses the
same mcp-use MCPClient/session primitives as the classic assistant and exposes
selected MCP tools to realtime providers as ordinary function tools.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import re
from typing import Any

from mcp_use import MCPClient

from .engine import RealtimeFunctionTool


DEFAULT_MCP_PROMPT_NAME = "agent_prompt"
DEFAULT_MCP_PROMPT_TOOL = "get_agent_prompt"


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


def substitute_env_vars(value: Any) -> Any:
    """Expand ${VAR}/$VAR strings before MCPClient.from_dict, matching Classic."""
    if isinstance(value, dict):
        return {str(key): substitute_env_vars(item) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute_env_vars(item) for item in value]
    if isinstance(value, str):
        return os.path.expandvars(value)
    return value


def _text_content(result: Any) -> str:
    parts: list[str] = []
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text is None and isinstance(item, dict):
            text = item.get("text")
        if text:
            parts.append(str(text).strip())
    return "\n".join(part for part in parts if part).strip()


def _prompt_result_text(result: Any) -> str:
    parts: list[str] = []
    for message in getattr(result, "messages", []) or []:
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        text = getattr(content, "text", None)
        if text is None and isinstance(content, dict):
            text = content.get("text")
        if text:
            parts.append(str(text).strip())
    return "\n".join(part for part in parts if part).strip()


async def load_mcp_prompt_from_session(
    session: Any,
    *,
    tool_names: set[str] | None = None,
    prompt_name: str = DEFAULT_MCP_PROMPT_NAME,
    tool_name: str = DEFAULT_MCP_PROMPT_TOOL,
) -> str:
    """Load optional MCP-owned instructions without adding domain semantics in LSA.

    Prefer the MCP prompts capability when the requested prompt is exposed. The
    historical get_agent_prompt tool remains a compatibility fallback.
    """
    try:
        prompts = await session.list_prompts()
    except Exception:
        prompts = []
    for prompt in prompts or []:
        name = str(getattr(prompt, "name", "") or "").strip()
        if name != prompt_name:
            continue
        try:
            result = await session.get_prompt(prompt_name)
        except Exception:
            break
        text = _prompt_result_text(result)
        if text:
            return text
        break

    if tool_names is None:
        try:
            tools = await session.list_tools()
        except Exception:
            tools = []
        tool_names = {
            str(getattr(tool, "name", "") or "").strip()
            for tool in tools or []
            if str(getattr(tool, "name", "") or "").strip()
        }
    if tool_name not in tool_names:
        return ""
    try:
        result = await session.call_tool(tool_name, {})
    except Exception:
        return ""
    if bool(getattr(result, "isError", False)):
        return ""
    return _text_content(result)


async def load_remote_mcp_prompt(
    *,
    server_name: str,
    url: str,
    authorization: str = "",
    headers: dict[str, str] | None = None,
    prompt_name: str = DEFAULT_MCP_PROMPT_NAME,
    tool_name: str = DEFAULT_MCP_PROMPT_TOOL,
) -> str:
    """Fetch optional instructions from a remote MCP using the generic MCP client."""
    request_headers = dict(headers or {})
    if authorization and not any(key.casefold() == "authorization" for key in request_headers):
        request_headers["Authorization"] = f"Bearer {authorization}"
    entry: dict[str, Any] = {"url": url}
    if request_headers:
        entry["headers"] = request_headers
    client = MCPClient.from_dict({"mcpServers": {server_name: entry}})
    try:
        await client.create_session(server_name)
        session = client.get_session(server_name)
        return await load_mcp_prompt_from_session(
            session,
            prompt_name=prompt_name,
            tool_name=tool_name,
        )
    finally:
        if getattr(client, "sessions", None):
            await client.close_all_sessions()


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
        config = substitute_env_vars(config)
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
        self._server_tool_names: dict[str, set[str]] = {}
        self._server_prompt_text: dict[str, str] = {}

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
        server_tool_names: dict[str, set[str]] = {}
        server_prompt_text: dict[str, str] = {}
        server_tools: dict[str, list[Any]] = {}

        for server_name in self.server_names:
            if server_name not in getattr(self.client, "sessions", {}):
                await self.client.create_session(server_name)
            session = self.client.get_session(server_name)
            tools = list(await session.list_tools() or [])
            server_tools[server_name] = tools
            names = {
                str(getattr(tool, "name", "") or "").strip()
                for tool in tools
                if str(getattr(tool, "name", "") or "").strip()
            }
            server_tool_names[server_name] = names
            if str(os.getenv("MCP_LOAD_SERVER_PROMPT", "true")).strip().lower() not in {"0", "false", "no", "off"}:
                server_prompt_text[server_name] = await load_mcp_prompt_from_session(session, tool_names=names)
            else:
                server_prompt_text[server_name] = ""

        for server_name, tools in server_tools.items():
            allowed = self.allowed_tools.get(server_name)
            context_instructions = server_prompt_text.get(server_name, "")
            for tool in tools:
                tool_name = str(getattr(tool, "name", "") or "").strip()
                if not tool_name:
                    continue
                if allowed is not None and tool_name not in allowed:
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
                        context_instructions=context_instructions,
                    )
                )
                targets[exposed_name] = BridgeToolTarget(server=server_name, tool=tool_name)

        self._tool_targets = targets
        self._function_tools = tuple(functions)
        self._server_tool_names = server_tool_names
        self._server_prompt_text = server_prompt_text
        return self._function_tools

    async def load_prompt_text(self, server_name: str) -> str:
        """Return MCP-owned instructions already loaded at bridge startup."""
        return self._server_prompt_text.get(server_name, "")

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
