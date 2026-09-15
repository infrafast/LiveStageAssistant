"""Native Ollama MCP execution for the supervised local Classic engine.

This module keeps local Ollama tool execution independent from ``mcp_use``'s
agent executor while reusing the same discovered LangChain MCP tools and MCP
sessions. It deliberately contains no mixer-, lighting-, or vendor-specific
tool logic.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from . import agent
from .speaker_recognition import SpeakerRecognitionResult


def _message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                parts.append(str(text if text is not None else item))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part).strip()
    return str(content or "").strip()


def _tool_result_text(result: Any) -> str:
    if isinstance(result, str):
        return result
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


class NativeOllamaMcpVoiceAssistant(agent.VoiceAssistant):
    """Local Ollama assistant using a direct LangChain tool-call loop.

    MCP keyword routing keeps its existing meaning: it narrows a turn to one
    configured MCP server when a route matches. When no route matches, all
    discovered MCP tools remain candidates.
    """

    async def refresh_session_llm_summary(self, *, force: bool = False) -> bool:
        """Avoid an expensive automatic Ollama inference before local READY."""
        if not force:
            print("Native Ollama MCP: automatic startup/session LLM summary refresh skipped.")
            return False
        return await super().refresh_session_llm_summary(force=True)

    async def _invoke_native_tool(self, tool: Any, arguments: dict[str, Any]) -> Any:
        if hasattr(tool, "ainvoke"):
            return await tool.ainvoke(arguments)
        if hasattr(tool, "invoke"):
            return await asyncio.to_thread(tool.invoke, arguments)
        raise RuntimeError(f"MCP tool '{getattr(tool, 'name', '<unnamed>')}' is not invokable")

    async def _run_native_ollama_tool_loop(self, agent_input: str, tools: list[Any]) -> str:
        loop_started = time.perf_counter()
        selected_tools = _unique_tools_by_name(list(tools or []))
        tool_by_name = {
            str(getattr(tool, "name", "") or ""): tool
            for tool in selected_tools
        }

        llm = getattr(self, "_native_ollama_llm", None)
        if llm is None:
            llm = self._build_llm()
            self._native_ollama_llm = llm
        runnable = llm.bind_tools(selected_tools) if selected_tools else llm

        messages: list[Any] = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=agent_input),
        ]
        deadline = time.monotonic() + max(1.0, float(self.mcp_agent_timeout_seconds))

        async def within_budget(awaitable):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError
            return await asyncio.wait_for(awaitable, timeout=remaining)

        max_steps = max(1, int(self.mcp_agent_max_steps))
        for step in range(1, max_steps + 1):
            llm_started = time.perf_counter()
            response = await within_budget(runnable.ainvoke(messages))
            llm_elapsed = time.perf_counter() - llm_started
            messages.append(response)
            tool_calls = list(getattr(response, "tool_calls", None) or [])
            print(
                f"[OLLAMA NATIVE MCP LLM: step={step} elapsed={llm_elapsed:.2f}s "
                f"tool_calls={len(tool_calls)} tools={len(selected_tools)}]"
            )
            if not tool_calls:
                text = _message_text(response)
                if not text:
                    raise RuntimeError("Native Ollama returned an empty response without a tool call")
                total_elapsed = time.perf_counter() - loop_started
                print(
                    f"[OLLAMA NATIVE MCP DONE: steps={step} total={total_elapsed:.2f}s "
                    f"tools={len(selected_tools)}]"
                )
                return text

            for index, tool_call in enumerate(tool_calls, start=1):
                call = dict(tool_call)
                tool_name = str(call.get("name") or "").strip()
                arguments = call.get("args")
                if not isinstance(arguments, dict):
                    arguments = {}
                call_id = str(call.get("id") or f"native_{step}_{index}")
                tool = tool_by_name.get(tool_name)
                if tool is None:
                    result_text = f"Tool '{tool_name}' is not available in the selected MCP tool set."
                    print(
                        f"[OLLAMA NATIVE MCP TOOL: {tool_name or '<missing>'} step={step} "
                        "elapsed=0.00s unavailable]"
                    )
                else:
                    tool_started = time.perf_counter()
                    try:
                        result = await within_budget(self._invoke_native_tool(tool, arguments))
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
                    ToolMessage(
                        content=result_text,
                        tool_call_id=call_id,
                    )
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

        agent_input = self._with_runtime_instructions(text, speaker_result=speaker_result)
        route = self._select_mcp_tool_route(text)
        confirmation_route = False
        if not route and self.pending_mcp_confirmation_route and self._is_mcp_confirmation_reply(text):
            route = self.pending_mcp_confirmation_route
            confirmation_route = True
            print(f"[MCP ROUTE: confirmation -> {route.get('server')}]")

        if not confirmation_route and not self._is_mcp_confirmation_reply(text):
            self.pending_mcp_confirmation_route = None

        selected_tools = list(self.mcp_all_tools or [])
        route_label = "all"
        if route:
            server_name = str(route.get("server") or "")
            routed_tools = list(self.mcp_tools_by_server.get(server_name) or [])
            if routed_tools:
                selected_tools = routed_tools
                route_label = server_name
            else:
                print(
                    f"[OLLAMA NATIVE MCP: route {server_name} has no mapped tools; "
                    "using all discovered tools]"
                )

        selected_tools = _unique_tools_by_name(selected_tools)
        print(
            f"[OLLAMA NATIVE MCP: route={route_label} tools={len(selected_tools)}]"
        )
        response = await self._run_native_ollama_tool_loop(agent_input, selected_tools)

        if route and self._assistant_response_requests_confirmation(response):
            self.pending_mcp_confirmation_route = route
        elif route:
            self.pending_mcp_confirmation_route = None
        return response
