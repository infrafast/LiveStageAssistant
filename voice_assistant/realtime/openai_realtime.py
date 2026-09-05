"""OpenAI Realtime provider adapter.

This module contains provider transport/protocol code only. Provider-native remote
MCP servers are translated from the provider-neutral realtime config; no mixer,
lighting or other domain-specific logic belongs here.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any
from urllib.parse import quote

from .engine import RealtimeEngine, RealtimeEngineConfig, RealtimeEngineState, RealtimeEvent


OPENAI_REALTIME_WS_BASE = "wss://api.openai.com/v1/realtime"


class OpenAIRealtimeEngine(RealtimeEngine):
    def __init__(
        self,
        config: RealtimeEngineConfig,
        *,
        api_key: str,
        websocket_url: str = OPENAI_REALTIME_WS_BASE,
    ) -> None:
        super().__init__(config)
        if not api_key.strip():
            raise ValueError("OpenAI API key is required")
        self.api_key = api_key.strip()
        self.websocket_url = websocket_url.rstrip("?")
        self._ws = None
        self._receiver_task: asyncio.Task | None = None
        self._events: asyncio.Queue[RealtimeEvent] = asyncio.Queue()
        self._response_active = False

    def _session_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        for server in self.config.mcp_servers:
            tool: dict[str, Any] = {
                "type": "mcp",
                "server_label": server.label,
                "server_url": server.url,
                "require_approval": server.require_approval,
            }
            if server.authorization:
                tool["authorization"] = server.authorization
            if server.headers:
                tool["headers"] = dict(server.headers)
            if server.allowed_tools:
                tool["allowed_tools"] = list(server.allowed_tools)
            tools.append(tool)
        return tools

    async def start(self) -> None:
        if self.state != RealtimeEngineState.STOPPED:
            return
        self.state = RealtimeEngineState.CONNECTING
        try:
            from websockets.asyncio.client import connect
        except ImportError as exc:
            self.state = RealtimeEngineState.ERROR
            raise RuntimeError("Realtime requires the 'websockets' Python package") from exc

        url = f"{self.websocket_url}?model={quote(self.config.model, safe='')}"
        self._ws = await connect(
            url,
            additional_headers={"Authorization": f"Bearer {self.api_key}"},
            max_size=16 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
        )
        self._receiver_task = asyncio.create_task(self._receive_loop(), name="openai-realtime-receiver")
        await self._send(
            {
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "output_modalities": ["audio"],
                    "instructions": self.config.instructions,
                    "tools": self._session_tools(),
                    "audio": {
                        "input": {
                            "format": {"type": "audio/pcm", "rate": 24000},
                            "noise_reduction": {"type": "near_field"},
                            "turn_detection": (
                                {
                                    "type": "server_vad",
                                    "threshold": 0.5,
                                    "prefix_padding_ms": 300,
                                    "silence_duration_ms": 500,
                                    "create_response": True,
                                    "interrupt_response": True,
                                }
                                if self.config.server_vad
                                else None
                            ),
                        },
                        "output": {
                            "format": {"type": "audio/pcm", "rate": 24000},
                            "voice": self.config.voice,
                            "speed": 1.0,
                        },
                    },
                },
            }
        )

    async def stop(self) -> None:
        task = self._receiver_task
        self._receiver_task = None
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._response_active = False
        self.state = RealtimeEngineState.STOPPED

    async def send_audio(self, pcm: bytes) -> None:
        if not pcm:
            return
        self._require_connection()
        await self._send(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm).decode("ascii"),
            }
        )

    async def commit_audio(self) -> None:
        self._require_connection()
        await self._send({"type": "input_audio_buffer.commit"})
        if not self.config.server_vad:
            await self._send({"type": "response.create"})

    async def next_event(self) -> RealtimeEvent:
        return await self._events.get()

    async def cancel_response(self) -> None:
        if self._ws is None or not self._response_active:
            return
        await self._send({"type": "response.cancel"})

    async def submit_tool_result(self, call_id: str, result: Any) -> None:
        self._require_connection()
        if not call_id:
            raise ValueError("call_id is required")
        output = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        await self._send(
            {
                "type": "conversation.item.create",
                "item": {"type": "function_call_output", "call_id": call_id, "output": output},
            }
        )
        await self._send({"type": "response.create"})

    async def _send(self, payload: dict[str, Any]) -> None:
        self._require_connection()
        await self._ws.send(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))

    def _require_connection(self) -> None:
        if self._ws is None:
            raise RuntimeError("realtime connection is not active")

    async def _receive_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    event = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue
                translated = self._translate_event(event)
                if translated is not None:
                    await self._events.put(translated)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.state = RealtimeEngineState.ERROR
            await self._events.put(RealtimeEvent("connection_error", {"error": str(exc)}))
        finally:
            if self.state != RealtimeEngineState.STOPPED:
                await self._events.put(RealtimeEvent("connection_closed", {}))

    def _translate_mcp_item(self, event_type: str, event: dict[str, Any]) -> RealtimeEvent | None:
        item = event.get("item") or {}
        item_type = str(item.get("type") or "")
        if item_type not in {"mcp_list_tools", "mcp_call", "mcp_approval_request"}:
            return None
        phase = "added" if event_type.endswith(".added") else "done" if event_type.endswith(".done") else "event"
        return RealtimeEvent(item_type, {"phase": phase, "item": item, "event": event})

    def _translate_event(self, event: dict[str, Any]) -> RealtimeEvent | None:
        event_type = str(event.get("type") or "")
        if event_type == "session.created":
            return RealtimeEvent("session_created", {"session": event.get("session") or {}})
        if event_type == "session.updated":
            self.state = RealtimeEngineState.READY
            return RealtimeEvent("ready", {"session": event.get("session") or {}})
        if event_type == "input_audio_buffer.speech_started":
            self.state = RealtimeEngineState.ACTIVE
            return RealtimeEvent("speech_started", event)
        if event_type == "input_audio_buffer.speech_stopped":
            return RealtimeEvent("speech_stopped", event)
        if event_type == "response.created":
            self._response_active = True
            self.state = RealtimeEngineState.ACTIVE
            return RealtimeEvent("response_started", {"response": event.get("response") or {}})
        if event_type in {"response.output_item.added", "response.output_item.done"}:
            mcp_event = self._translate_mcp_item(event_type, event)
            if mcp_event is not None:
                return mcp_event
        if event_type == "response.output_audio.delta":
            try:
                audio = base64.b64decode(event.get("delta") or "", validate=True)
            except Exception:
                audio = b""
            return RealtimeEvent(
                "audio_delta",
                {"audio": audio, "response_id": str(event.get("response_id") or "")},
            )
        if event_type == "response.output_audio_transcript.delta":
            return RealtimeEvent(
                "transcript_delta",
                {"text": str(event.get("delta") or ""), "response_id": str(event.get("response_id") or "")},
            )
        if event_type == "response.output_audio_transcript.done":
            return RealtimeEvent(
                "transcript_done",
                {"text": str(event.get("transcript") or ""), "response_id": str(event.get("response_id") or "")},
            )
        if event_type == "response.done":
            self._response_active = False
            self.state = RealtimeEngineState.READY
            response = event.get("response") or {}
            return RealtimeEvent(
                "response_done",
                {
                    "response_id": str(response.get("id") or ""),
                    "status": response.get("status"),
                    "usage": response.get("usage") or {},
                    "response": response,
                },
            )
        if event_type == "error":
            error = event.get("error") or {}
            return RealtimeEvent("provider_error", {"error": error})
        if "mcp" in event_type:
            return RealtimeEvent("mcp_event", {"event_type": event_type, "event": event})
        return None
