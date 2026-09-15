"""Native Ollama MCP execution for the supervised local Classic engine.

This module keeps local Ollama tool execution independent from ``mcp_use``'s
agent executor while reusing the same discovered LangChain MCP tool objects and
MCP sessions. It deliberately contains no mixer-, lighting-, or vendor-specific
tool logic.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any
import urllib.error
import urllib.request

from . import agent
from .speaker_recognition import SpeakerRecognitionResult


_MCP_PROMPT_MARKER = "\nAdditional instructions loaded from MCP servers:"
_MCP_SERVER_HEADER = re.compile(
    r'Instructions loaded from MCP server "([^"]+)":\s*\n'
)

_LOCAL_SYSTEM_PROMPT = (
    "You are Live Stage Assistant, a conservative tool-driven voice assistant. "
    "Reply in the user's language. Use only provided tools. For live external "
    "state or actions, use tools and never invent state, targets, indexes, "
    "capabilities or results. If a tool is needed, call it before any text. "
    "Ask for clarification when the target or action is ambiguous. Never claim "
    "success unless the tool result confirms it. Keep the final answer concise "
    "plain text."
)
_LOCAL_OLLAMA_NUM_CTX = 2048
_LOCAL_OLLAMA_NUM_PREDICT = 128
_LOCAL_OLLAMA_TEMPERATURE = 0.0


def _tool_result_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    if hasattr(result, "model_dump"):
        try:
            result = result.model_dump(mode="json")
        except Exception:
            pass
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(result)


def _unique_tools_by_name(tools: list[Any]) -> list[Any]:
    """Keep one tool per callable name because tool-call protocols key by name."""
    unique: list[Any] = []
    seen: set[str] = set()
    for tool in tools:
        name = str(getattr(tool, "name", "") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        unique.append(tool)
    return unique


def _tool_input_schema(tool: Any) -> dict[str, Any]:
    """Return the JSON input schema exposed by a generic LangChain MCP tool."""
    candidates: list[Any] = [
        getattr(tool, "args_schema", None),
        getattr(tool, "tool_call_schema", None),
    ]
    get_input_schema = getattr(tool, "get_input_schema", None)
    if callable(get_input_schema):
        try:
            candidates.append(get_input_schema())
        except Exception:
            pass

    for candidate in candidates:
        if candidate is None:
            continue
        if isinstance(candidate, dict):
            schema = dict(candidate)
        elif hasattr(candidate, "model_json_schema"):
            try:
                schema = candidate.model_json_schema()
            except Exception:
                continue
        elif hasattr(candidate, "schema"):
            try:
                schema = candidate.schema()
            except Exception:
                continue
        else:
            continue
        if isinstance(schema, dict):
            schema = dict(schema)
            schema.pop("title", None)
            schema.setdefault("type", "object")
            schema.setdefault("properties", {})
            return schema

    return {"type": "object", "properties": {}}


def _ollama_tool_schema(tool: Any) -> dict[str, Any]:
    """Convert a generic LangChain MCP tool into Ollama's function schema."""
    name = str(getattr(tool, "name", "") or "").strip()
    if not name:
        raise ValueError("MCP tool has no callable name")
    description = str(getattr(tool, "description", "") or "").strip()
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": _tool_input_schema(tool),
        },
    }


def _mcp_instruction_blocks(system_prompt: str) -> tuple[str, list[tuple[str, str]]]:
    """Split the consolidated prompt into base text and MCP-owned instruction blocks."""
    prompt = str(system_prompt or "").strip()
    if not prompt or _MCP_PROMPT_MARKER not in prompt:
        return prompt, []

    base_prompt, _, mcp_section = prompt.partition(_MCP_PROMPT_MARKER)
    matches = list(_MCP_SERVER_HEADER.finditer(mcp_section))
    blocks: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        body_start = match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(mcp_section)
        body = mcp_section[body_start:body_end].strip()
        if body:
            blocks.append((match.group(1), body))
    return base_prompt.strip(), blocks


def _compact_local_prompt(system_prompt: str, server_name: str | None) -> str:
    """Build the small-model prompt without re-sending the verbose Classic base prompt.

    The MCP server remains authoritative for domain instructions. Routing only
    chooses which server block is relevant; when there is no route, every MCP
    block remains present so routing is never repurposed as a tool/no-tool gate.
    If a non-standard prompt has no MCP merge markers, keep it after the compact
    safety contract instead of silently discarding custom instructions.
    """
    base_prompt, blocks = _mcp_instruction_blocks(system_prompt)
    if not blocks:
        if base_prompt and base_prompt != _LOCAL_SYSTEM_PROMPT:
            return f"{_LOCAL_SYSTEM_PROMPT}\n\n{base_prompt}".strip()
        return _LOCAL_SYSTEM_PROMPT

    selected_blocks = blocks
    if server_name:
        matching = [(name, body) for name, body in blocks if name == server_name]
        if matching:
            selected_blocks = matching

    parts = [_LOCAL_SYSTEM_PROMPT]
    for name, body in selected_blocks:
        parts.append(f'MCP server "{name}" instructions:\n{body}')
    return "\n\n".join(parts).strip()


def _duration_seconds(payload: dict[str, Any], key: str) -> float:
    value = payload.get(key)
    if not isinstance(value, (int, float)):
        return 0.0
    return float(value) / 1_000_000_000.0


class NativeOllamaMcpVoiceAssistant(agent.VoiceAssistant):
    """Local Ollama assistant using Ollama's native /api/chat tool loop.

    MCP keyword routing keeps its existing meaning: it narrows a turn to one
    configured MCP server when a route matches. When no route matches, every
    discovered MCP *tool* remains a candidate. MCP resource/prompt wrappers
    created by mcp_use are not action tools and are therefore not sent to
    Ollama as callable functions.
    """

    async def refresh_session_llm_summary(self, *, force: bool = False) -> bool:
        """Avoid an expensive automatic Ollama inference before local READY."""
        if not force:
            print("Native Ollama MCP: automatic startup/session LLM summary refresh skipped.")
            return False
        return await super().refresh_session_llm_summary(force=True)

    def _native_callable_mcp_tools(self) -> list[Any]:
        """Return only actual MCP tools, excluding mcp_use resource/prompt wrappers."""
        adapter = getattr(self.agent, "adapter", None) if self.agent else None
        if adapter is not None and hasattr(adapter, "tools"):
            return _unique_tools_by_name(list(getattr(adapter, "tools", []) or []))
        return _unique_tools_by_name(list(self.mcp_all_tools or []))

    def _native_routed_tools(self, server_name: str, native_tools: list[Any]) -> list[Any]:
        """Intersect a routed server subset with actual MCP tools by object identity."""
        routed = list(self.mcp_tools_by_server.get(server_name) or [])
        routed_ids = {id(tool) for tool in routed}
        return [tool for tool in native_tools if id(tool) in routed_ids]

    def _native_agent_input(
        self,
        text: str,
        speaker_result: SpeakerRecognitionResult | None,
    ) -> str:
        """Avoid Classic runtime boilerplate when there is no speaker context to convey."""
        clean_text = str(text or "").strip()
        if speaker_result is None:
            return clean_text
        return self._with_runtime_instructions(clean_text, speaker_result=speaker_result)

    async def _invoke_native_tool(self, tool: Any, arguments: dict[str, Any]) -> Any:
        if hasattr(tool, "ainvoke"):
            return await tool.ainvoke(arguments)
        if hasattr(tool, "invoke"):
            return await asyncio.to_thread(tool.invoke, arguments)
        raise RuntimeError(f"MCP tool '{getattr(tool, 'name', '<unnamed>')}' is not invokable")

    def _native_ollama_payload(
        self,
        messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": False,
            "keep_alive": "10m",
            "options": {
                "temperature": _LOCAL_OLLAMA_TEMPERATURE,
                "num_ctx": _LOCAL_OLLAMA_NUM_CTX,
                "num_predict": _LOCAL_OLLAMA_NUM_PREDICT,
            },
        }
        if tool_schemas:
            payload["tools"] = tool_schemas
        return payload

    def _native_ollama_chat_sync(
        self,
        messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]],
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """Call the local Ollama REST API directly, without LangChain model execution."""
        payload = self._native_ollama_payload(messages, tool_schemas)
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        endpoint = f"{str(self.ollama_base_url).rstrip('/')}/api/chat"
        request = urllib.request.Request(
            endpoint,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=max(1.0, float(timeout_seconds)),
            ) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            detail = ""
            try:
                detail = error.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise RuntimeError(
                f"Ollama /api/chat HTTP {error.code}: {detail or error.reason}"
            ) from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"Ollama /api/chat unavailable: {error.reason}") from error

        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError("Ollama /api/chat returned invalid JSON") from error
        if not isinstance(result, dict) or not isinstance(result.get("message"), dict):
            raise RuntimeError("Ollama /api/chat returned no assistant message")
        return result

    async def _native_ollama_chat(
        self,
        messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]],
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._native_ollama_chat_sync,
            messages,
            tool_schemas,
            timeout_seconds=timeout_seconds,
        )

    async def _run_native_ollama_tool_loop(
        self,
        agent_input: str,
        tools: list[Any],
        *,
        route_server: str | None = None,
    ) -> str:
        loop_started = time.perf_counter()
        selected_tools = _unique_tools_by_name(list(tools or []))
        tool_by_name = {
            str(getattr(tool, "name", "") or ""): tool
            for tool in selected_tools
        }
        tool_schemas = [_ollama_tool_schema(tool) for tool in selected_tools]
        schema_chars = len(
            json.dumps(tool_schemas, ensure_ascii=False, separators=(",", ":"))
        )

        turn_prompt = _compact_local_prompt(self.system_prompt, route_server)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": turn_prompt},
            {"role": "user", "content": agent_input},
        ]
        deadline = time.monotonic() + max(1.0, float(self.mcp_agent_timeout_seconds))

        print(
            f"[OLLAMA NATIVE MCP CONTEXT: route={route_server or 'all'} "
            f"prompt_chars={len(turn_prompt)} input_chars={len(agent_input)} "
            f"schema_chars={schema_chars} tools={len(selected_tools)} "
            f"num_ctx={_LOCAL_OLLAMA_NUM_CTX} num_predict={_LOCAL_OLLAMA_NUM_PREDICT}]"
        )

        def remaining_budget() -> float:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError
            return remaining

        max_steps = max(1, int(self.mcp_agent_max_steps))
        for step in range(1, max_steps + 1):
            remaining = remaining_budget()
            payload = self._native_ollama_payload(messages, tool_schemas)
            payload_chars = len(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            )
            print(
                f"[OLLAMA NATIVE API START: step={step} route={route_server or 'all'} "
                f"payload_chars={payload_chars} tools={len(selected_tools)} "
                f"budget={remaining:.2f}s]"
            )
            llm_started = time.perf_counter()
            response = await asyncio.wait_for(
                self._native_ollama_chat(
                    messages,
                    tool_schemas,
                    timeout_seconds=remaining,
                ),
                timeout=remaining,
            )
            llm_elapsed = time.perf_counter() - llm_started

            assistant_message = dict(response.get("message") or {})
            content = str(assistant_message.get("content") or "").strip()
            tool_calls = list(assistant_message.get("tool_calls") or [])
            history_message: dict[str, Any] = {
                "role": "assistant",
                "content": content,
            }
            if tool_calls:
                history_message["tool_calls"] = tool_calls
            messages.append(history_message)

            print(
                f"[OLLAMA NATIVE API: step={step} elapsed={llm_elapsed:.2f}s "
                f"server_total={_duration_seconds(response, 'total_duration'):.2f}s "
                f"load={_duration_seconds(response, 'load_duration'):.2f}s "
                f"prompt_eval={_duration_seconds(response, 'prompt_eval_duration'):.2f}s "
                f"prompt_tokens={int(response.get('prompt_eval_count') or 0)} "
                f"eval={_duration_seconds(response, 'eval_duration'):.2f}s "
                f"eval_tokens={int(response.get('eval_count') or 0)} "
                f"tool_calls={len(tool_calls)} tools={len(selected_tools)}]"
            )

            if not tool_calls:
                if not content:
                    raise RuntimeError(
                        "Native Ollama returned an empty response without a tool call"
                    )
                total_elapsed = time.perf_counter() - loop_started
                print(
                    f"[OLLAMA NATIVE MCP DONE: steps={step} total={total_elapsed:.2f}s "
                    f"tools={len(selected_tools)}]"
                )
                return content

            for tool_call in tool_calls:
                function = tool_call.get("function") if isinstance(tool_call, dict) else None
                function = function if isinstance(function, dict) else {}
                tool_name = str(function.get("name") or "").strip()
                arguments = function.get("arguments")
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                if not isinstance(arguments, dict):
                    arguments = {}

                tool = tool_by_name.get(tool_name)
                if tool is None:
                    result_text = (
                        f"Tool '{tool_name}' is not available in the selected MCP tool set."
                    )
                    print(
                        f"[OLLAMA NATIVE MCP TOOL: {tool_name or '<missing>'} step={step} "
                        "elapsed=0.00s unavailable]"
                    )
                else:
                    tool_started = time.perf_counter()
                    try:
                        result = await asyncio.wait_for(
                            self._invoke_native_tool(tool, arguments),
                            timeout=remaining_budget(),
                        )
                        result_text = _tool_result_text(result)
                    except asyncio.TimeoutError:
                        raise
                    except Exception as error:
                        result_text = f"Tool '{tool_name}' failed: {error}"
                    tool_elapsed = time.perf_counter() - tool_started
                    print(
                        f"[OLLAMA NATIVE MCP TOOL: {tool_name} step={step} "
                        f"elapsed={tool_elapsed:.3f}s tools={len(selected_tools)}]"
                    )

                messages.append(
                    {
                        "role": "tool",
                        "tool_name": tool_name,
                        "content": result_text,
                    }
                )

        raise RuntimeError(
            f"Native Ollama MCP exceeded the configured max steps ({max_steps})"
        )

    async def _run_agent_with_optional_tool_routing(
        self,
        text: str,
        speaker_result: SpeakerRecognitionResult | None = None,
    ) -> str:
        if self.llm_provider != "ollama":
            return await super()._run_agent_with_optional_tool_routing(
                text,
                speaker_result=speaker_result,
            )

        agent_input = self._native_agent_input(text, speaker_result)
        route = self._select_mcp_tool_route(text)
        confirmation_route = False
        if not route and self.pending_mcp_confirmation_route and self._is_mcp_confirmation_reply(text):
            route = self.pending_mcp_confirmation_route
            confirmation_route = True
            print(f"[MCP ROUTE: confirmation -> {route.get('server')}]")

        if not confirmation_route and not self._is_mcp_confirmation_reply(text):
            self.pending_mcp_confirmation_route = None

        native_tools = self._native_callable_mcp_tools()
        selected_tools = native_tools
        route_label = "all"
        route_server: str | None = None
        if route:
            server_name = str(route.get("server") or "")
            routed_tools = self._native_routed_tools(server_name, native_tools)
            if routed_tools:
                selected_tools = routed_tools
                route_label = server_name
                route_server = server_name
            else:
                print(
                    f"[OLLAMA NATIVE MCP: route {server_name} has no callable MCP tools; "
                    "using all discovered MCP tools]"
                )

        selected_tools = _unique_tools_by_name(selected_tools)
        print(
            f"[OLLAMA NATIVE MCP: route={route_label} tools={len(selected_tools)}]"
        )
        response = await self._run_native_ollama_tool_loop(
            agent_input,
            selected_tools,
            route_server=route_server,
        )

        if route and self._assistant_response_requests_confirmation(response):
            self.pending_mcp_confirmation_route = route
        elif route:
            self.pending_mcp_confirmation_route = None
        return response
