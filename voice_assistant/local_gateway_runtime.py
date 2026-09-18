"""Deterministic Local MCP command-gateway orchestration.

This module is intentionally domain-neutral. Mixer/QLC language stays inside
participating MCP servers. LSA only discovers the versioned gateway contract,
routes candidate servers, arbitrates claims, applies write approval policy and
executes one opaque plan.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
import re
import unicodedata
from typing import Any, Mapping

from mcp_use import MCPClient

GATEWAY_PROTOCOL = "lsa-command-gateway/v1"
ANALYZE_TOOL = "lsa_local_analyze_command"
EXECUTE_TOOL = "lsa_local_execute_command"

YES_WORDS = {"oui", "yes", "ok", "okay", "confirme", "confirm", "vas y", "go"}
NO_WORDS = {"non", "no", "annule", "annuler", "cancel", "stop"}


@dataclass(frozen=True)
class LocalGatewayServer:
    name: str
    permission: str
    routing: tuple[str, ...]


@dataclass(frozen=True)
class PendingContinuation:
    server: str
    token: str


@dataclass(frozen=True)
class PendingApproval:
    server: str
    plan_token: str


def _normalized(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold().replace("’", "'")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _routing_words(entry: Mapping[str, Any]) -> tuple[str, ...]:
    options = entry.get("assistantOptions")
    if not isinstance(options, Mapping):
        return ()
    raw = options.get("routing")
    if isinstance(raw, (list, tuple)):
        parts = [str(item) for item in raw]
    else:
        parts = re.split(r"[,;\n]+", str(raw or ""))
    result: list[str] = []
    seen: set[str] = set()
    for item in parts:
        word = _normalized(item)
        if word and word not in seen:
            seen.add(word)
            result.append(word)
    return tuple(result)


def _permission(entry: Mapping[str, Any]) -> str:
    realtime = entry.get("realtime")
    if not isinstance(realtime, Mapping):
        return "open"
    permissions = realtime.get("permissions")
    if isinstance(permissions, str):
        mode = permissions
    elif isinstance(permissions, Mapping):
        mode = permissions.get("mode")
    else:
        mode = realtime.get("permission")
    value = str(mode or "open").strip().lower()
    return value if value in {"open", "approval"} else "approval"


def _enabled(entry: Mapping[str, Any]) -> bool:
    return entry.get("enabled", True) is not False


def _expand_env(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _expand_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, tuple):
        return [_expand_env(item) for item in value]
    if isinstance(value, str):
        return os.path.expandvars(value)
    return value


def _local_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    local = {
        str(key): value
        for key, value in entry.items()
        if key not in {"native", "realtime", "assistantOptions", "assistantPrompt", "agentPrompt", "enabled"}
    }
    command = str(local.get("command") or "").strip()
    if command:
        env = local.get("env")
        env_map = dict(env) if isinstance(env, Mapping) else {}
        env_map["LSA_LOCAL_COMMAND_GATEWAY"] = "1"
        local["env"] = env_map
    return _expand_env(local)


def local_gateway_mcp_config(config: Mapping[str, Any]) -> dict[str, Any]:
    servers = config.get("mcpServers")
    if not isinstance(servers, Mapping):
        return {"mcpServers": {}}
    return {
        "mcpServers": {
            str(name): _local_entry(entry)
            for name, entry in servers.items()
            if isinstance(entry, Mapping) and _enabled(entry)
        }
    }


def _tool_name(tool: Any) -> str:
    if isinstance(tool, Mapping):
        return str(tool.get("name") or "").strip()
    return str(getattr(tool, "name", "") or "").strip()


def _tool_schema(tool: Any) -> dict[str, Any]:
    if isinstance(tool, Mapping):
        schema = tool.get("inputSchema") or tool.get("input_schema")
    else:
        schema = getattr(tool, "inputSchema", None)
        if schema is None:
            schema = getattr(tool, "input_schema", None)
    if hasattr(schema, "model_dump"):
        schema = schema.model_dump()
    return dict(schema) if isinstance(schema, Mapping) else {}


def _supports_protocol(tool: Any) -> bool:
    schema = _tool_schema(tool)
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return False
    protocol = properties.get("protocol")
    if not isinstance(protocol, Mapping):
        return False
    enum = protocol.get("enum")
    if isinstance(enum, list):
        return GATEWAY_PROTOCOL in {str(item) for item in enum}
    const = protocol.get("const")
    return str(const or "") == GATEWAY_PROTOCOL


def _text_content(result: Any) -> str:
    parts: list[str] = []
    for item in getattr(result, "content", []) or []:
        text = item.get("text") if isinstance(item, Mapping) else getattr(item, "text", None)
        if text:
            parts.append(str(text).strip())
    return "\n".join(part for part in parts if part).strip()


def _gateway_payload(result: Any) -> dict[str, Any]:
    if bool(getattr(result, "isError", False)):
        raise RuntimeError(_text_content(result) or "MCP gateway tool returned an error")
    raw = _text_content(result)
    if not raw:
        structured = getattr(result, "structuredContent", None)
        if isinstance(structured, Mapping):
            return dict(structured)
        raise RuntimeError("MCP gateway returned no payload")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"MCP gateway returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("MCP gateway payload must be an object")
    if str(payload.get("protocol") or "") != GATEWAY_PROTOCOL:
        raise RuntimeError("MCP gateway protocol mismatch")
    return payload


class DeterministicGatewayOrchestrator:
    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        client: MCPClient | None = None,
        locale: str = "fr",
    ) -> None:
        servers = config.get("mcpServers")
        self.raw_config = dict(config)
        self.server_entries = dict(servers) if isinstance(servers, Mapping) else {}
        self.locale = str(locale or "fr")
        self.client = client or MCPClient.from_dict(local_gateway_mcp_config(config))
        self._owns_client = client is None
        self.servers: dict[str, LocalGatewayServer] = {}
        self.pending_continuation: PendingContinuation | None = None
        self.pending_approval: PendingApproval | None = None

    async def start(self) -> tuple[str, ...]:
        discovered: dict[str, LocalGatewayServer] = {}
        for name, raw_entry in self.server_entries.items():
            if not isinstance(raw_entry, Mapping) or not _enabled(raw_entry):
                continue
            try:
                if str(name) not in getattr(self.client, "sessions", {}):
                    await self.client.create_session(str(name))
                session = self.client.get_session(str(name))
                tools = list(await session.list_tools() or [])
            except Exception as exc:
                print(f"Local gateway unavailable: {name}: {exc}", flush=True)
                continue
            by_name = {_tool_name(tool): tool for tool in tools if _tool_name(tool)}
            analyze = by_name.get(ANALYZE_TOOL)
            execute = by_name.get(EXECUTE_TOOL)
            if not analyze or not execute or not _supports_protocol(analyze) or not _supports_protocol(execute):
                print(f"Local gateway unsupported: {name}: {GATEWAY_PROTOCOL} tools not exposed", flush=True)
                continue
            discovered[str(name)] = LocalGatewayServer(
                name=str(name),
                permission=_permission(raw_entry),
                routing=_routing_words(raw_entry),
            )
            print(f"LSA Local gateway: {name} protocol={GATEWAY_PROTOCOL} permission={_permission(raw_entry)}", flush=True)
        self.servers = discovered
        return tuple(discovered)

    def _routed_servers(self, text: str) -> tuple[str, ...]:
        normalized = f" {_normalized(text)} "
        matches: list[str] = []
        for server in self.servers.values():
            if any(f" {word} " in normalized for word in server.routing):
                matches.append(server.name)
        return tuple(matches) if matches else tuple(self.servers)

    async def _analyze(
        self,
        server: str,
        text: str,
        *,
        continuation_token: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        session = self.client.get_session(server)
        args: dict[str, Any] = {
            "protocol": GATEWAY_PROTOCOL,
            "text": text,
            "locale": self.locale,
        }
        if continuation_token:
            args["continuationToken"] = continuation_token
        if context:
            args["context"] = dict(context)
        result = await session.call_tool(ANALYZE_TOOL, args)
        return _gateway_payload(result)

    async def _execute(self, server: str, plan_token: str) -> dict[str, Any]:
        session = self.client.get_session(server)
        result = await session.call_tool(
            EXECUTE_TOOL,
            {"protocol": GATEWAY_PROTOCOL, "planToken": plan_token},
        )
        return _gateway_payload(result)

    async def handle(
        self,
        text: str,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> str:
        normalized = _normalized(text)

        if self.pending_approval:
            pending = self.pending_approval
            if normalized in YES_WORDS:
                self.pending_approval = None
                payload = await self._execute(pending.server, pending.plan_token)
                return str(payload.get("responseText") or ("Commande exécutée." if payload.get("ok") else "La commande a échoué."))
            if normalized in NO_WORDS:
                self.pending_approval = None
                return "Commande annulée."
            self.pending_approval = None

        if self.pending_continuation:
            pending = self.pending_continuation
            self.pending_continuation = None
            payload = await self._analyze(
                pending.server,
                text,
                continuation_token=pending.token,
                context=context,
            )
            return await self._resolve_single_claim(pending.server, payload)

        candidates = self._routed_servers(text)
        if not candidates:
            return "Commande non reconnue."

        results = await asyncio.gather(
            *(self._analyze(server, text, context=context) for server in candidates),
            return_exceptions=True,
        )
        claims: list[tuple[str, dict[str, Any]]] = []
        for server, result in zip(candidates, results):
            if isinstance(result, Exception):
                print(f"Local gateway analysis failed: {server}: {result}", flush=True)
                continue
            if result.get("recognized") is True and str(result.get("status") or "") in {"ready", "clarification"}:
                claims.append((server, result))

        if not claims:
            return "Commande non reconnue."
        if len(claims) > 1:
            names = ", ".join(server for server, _payload in claims)
            return f"Plusieurs contrôleurs reconnaissent cette commande ({names}). Précise le domaine."
        server, payload = claims[0]
        return await self._resolve_single_claim(server, payload)

    async def _resolve_single_claim(self, server: str, payload: Mapping[str, Any]) -> str:
        status = str(payload.get("status") or "")
        if status == "clarification":
            token = str(payload.get("continuationToken") or "")
            if token:
                self.pending_continuation = PendingContinuation(server=server, token=token)
            return str(payload.get("responseText") or "Précise la cible.")

        if status != "ready":
            return str(payload.get("responseText") or "Commande non reconnue.")

        plan_token = str(payload.get("planToken") or "")
        if not plan_token:
            return "Le contrôleur n'a pas fourni de plan exécutable."

        effect = str(payload.get("effect") or "none")
        server_config = self.servers.get(server)
        if effect == "write" and server_config and server_config.permission == "approval":
            self.pending_approval = PendingApproval(server=server, plan_token=plan_token)
            return "Cette commande va effectuer une action. Confirmer ?"

        executed = await self._execute(server, plan_token)
        if executed.get("ok") is True:
            return str(executed.get("responseText") or "Commande exécutée.")
        return str(executed.get("responseText") or "La commande a échoué.")

    async def close(self) -> None:
        if self._owns_client and getattr(self.client, "sessions", None):
            await self.client.close_all_sessions()
