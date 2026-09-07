"""Gemini Live provider adapter for the provider-neutral realtime contract."""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any
from urllib.parse import quote

from .audio import Pcm16MonoResampler
from .engine import RealtimeEngine, RealtimeEngineConfig, RealtimeEngineState, RealtimeEvent

GEMINI_LIVE_WS = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)


class GeminiLiveEngine(RealtimeEngine):
    """Raw-WebSocket Gemini Live adapter.

    LSA keeps MCP execution provider-neutral by exposing bridge-discovered MCP
    tools as ordinary function declarations. Provider-native MCP is therefore
    not required for this adapter.
    """

    def __init__(
        self,
        config: RealtimeEngineConfig,
        *,
        api_key: str,
        websocket_url: str = GEMINI_LIVE_WS,
    ) -> None:
        super().__init__(config)
        if not api_key.strip():
            raise ValueError("Gemini API key is required")
        self.api_key = api_key.strip()
        self.websocket_url = websocket_url
        self._ws = None
        self._receiver_task: asyncio.Task | None = None
        self._events: asyncio.Queue[RealtimeEvent] = asyncio.Queue()
        self._input_resampler = Pcm16MonoResampler(24000, 16000)
        self._turn = 0
        self._active_turn = ""
        self._cancelled = False
        self._last_usage: dict[str, Any] = {}
        self._call_names: dict[str, str] = {}

    def _function_declarations(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": dict(tool.parameters),
            }
            for tool in self.config.function_tools
        ]

    async def start(self) -> None:
        if self.state != RealtimeEngineState.STOPPED:
            return
        self.state = RealtimeEngineState.CONNECTING
        try:
            from websockets.asyncio.client import connect
        except ImportError as exc:
            self.state = RealtimeEngineState.ERROR
            raise RuntimeError("Gemini Live requires the 'websockets' Python package") from exc

        url = f"{self.websocket_url}?key={quote(self.api_key, safe='')}"
        self._ws = await connect(
            url,
            max_size=16 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
        )
        self._receiver_task = asyncio.create_task(self._receive_loop(), name="gemini-live-receiver")

        setup: dict[str, Any] = {
            "model": f"models/{self.config.model}",
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {"voiceName": self.config.voice}
                    }
                },
            },
            "systemInstruction": {"parts": [{"text": self.config.instructions}]},
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
        }
        functions = self._function_declarations()
        if functions:
            setup["tools"] = [{"functionDeclarations": functions}]
        await self._send({"setup": setup})

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
        self._input_resampler = Pcm16MonoResampler(24000, 16000)
        self._active_turn = ""
        self._cancelled = False
        self._call_names.clear()
        self.state = RealtimeEngineState.STOPPED

    async def send_audio(self, pcm: bytes) -> None:
        if not pcm:
            return
        converted = self._input_resampler.process(pcm)
        if not converted:
            return
        await self._send(
            {
                "realtimeInput": {
                    "audio": {
                        "data": base64.b64encode(converted).decode("ascii"),
                        "mimeType": "audio/pcm;rate=16000",
                    }
                }
            }
        )

    async def send_text(self, text: str, *, create_response: bool = True) -> None:
        value = str(text or "").strip()
        if not value:
            raise ValueError("text is required")
        # realtimeInput text is the low-latency Live API path; Live decides when
        # to produce the response, so create_response is intentionally implicit.
        await self._send({"realtimeInput": {"text": value}})

    async def commit_audio(self) -> None:
        await self._send({"realtimeInput": {"audioStreamEnd": True}})

    async def next_event(self) -> RealtimeEvent:
        return await self._events.get()

    async def cancel_response(self) -> None:
        # Gemini Live performs barge-in through its activity detection. LSA also
        # suppresses already-buffered audio locally after this call.
        self._cancelled = True

    async def submit_tool_result(self, call_id: str, result: Any) -> None:
        if not call_id:
            raise ValueError("call_id is required")
        payload = result if isinstance(result, dict) else {"result": result}
        name = self._call_names.pop(call_id, "")
        if not name:
            raise ValueError(f"unknown Gemini function call id: {call_id}")
        await self._send(
            {
                "toolResponse": {
                    "functionResponses": [
                        {"id": call_id, "name": name, "response": payload}
                    ]
                }
            }
        )

    async def _send(self, payload: dict[str, Any]) -> None:
        if self._ws is None:
            raise RuntimeError("realtime connection is not active")
        await self._ws.send(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

    async def _receive_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    message = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue
                for event in self._translate(message):
                    await self._events.put(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.state = RealtimeEngineState.ERROR
            await self._events.put(RealtimeEvent("connection_error", {"error": str(exc)}))
        finally:
            if self.state != RealtimeEngineState.STOPPED:
                await self._events.put(RealtimeEvent("connection_closed", {}))

    def _new_turn(self) -> str:
        self._turn += 1
        self._active_turn = f"gemini-turn-{self._turn}"
        self._cancelled = False
        return self._active_turn

    def _translate(self, message: dict[str, Any]) -> list[RealtimeEvent]:
        events: list[RealtimeEvent] = []
        if "setupComplete" in message:
            self.state = RealtimeEngineState.READY
            events.append(RealtimeEvent("ready", {"session": message.get("setupComplete") or {}}))
            return events

        if isinstance(message.get("usageMetadata"), dict):
            self._last_usage = dict(message["usageMetadata"])

        tool_call = message.get("toolCall")
        if isinstance(tool_call, dict):
            for call in tool_call.get("functionCalls") or []:
                if not isinstance(call, dict):
                    continue
                call_id = str(call.get("id") or "")
                name = str(call.get("name") or "")
                if not call_id:
                    call_id = f"{self._active_turn or self._new_turn()}:{name}"
                self._call_names[call_id] = name
                events.append(
                    RealtimeEvent(
                        "tool_call",
                        {
                            "call_id": call_id,
                            "name": name,
                            "arguments": json.dumps(call.get("args") or {}, ensure_ascii=False),
                            "response_id": self._active_turn,
                        },
                    )
                )

        content = message.get("serverContent")
        if not isinstance(content, dict):
            return events

        if content.get("interrupted"):
            response_id = self._active_turn
            self._cancelled = True
            events.append(RealtimeEvent("response_done", {"response_id": response_id, "status": "cancelled", "usage": self._last_usage}))
            self._active_turn = ""
            self.state = RealtimeEngineState.READY
            return events

        input_tx = content.get("inputTranscription")
        if isinstance(input_tx, dict) and str(input_tx.get("text") or "").strip():
            events.append(RealtimeEvent("user_transcript_done", {"text": str(input_tx.get("text") or "").strip()}))

        output_tx = content.get("outputTranscription")
        if isinstance(output_tx, dict) and str(output_tx.get("text") or "").strip():
            events.append(RealtimeEvent("transcript_done", {"text": str(output_tx.get("text") or "").strip(), "response_id": self._active_turn}))

        model_turn = content.get("modelTurn")
        parts = model_turn.get("parts") if isinstance(model_turn, dict) else []
        for part in parts or []:
            if not isinstance(part, dict):
                continue
            inline = part.get("inlineData")
            if isinstance(inline, dict) and str(inline.get("data") or ""):
                if not self._active_turn:
                    response_id = self._new_turn()
                    self.state = RealtimeEngineState.ACTIVE
                    events.append(RealtimeEvent("response_started", {"response": {"id": response_id}}))
                try:
                    audio = base64.b64decode(str(inline.get("data") or ""), validate=True)
                except Exception:
                    audio = b""
                if audio and not self._cancelled:
                    events.append(RealtimeEvent("audio_delta", {"audio": audio, "response_id": self._active_turn}))
            text = str(part.get("text") or "").strip()
            if text:
                events.append(RealtimeEvent("transcript_delta", {"text": text, "response_id": self._active_turn}))

        if content.get("turnComplete"):
            response_id = self._active_turn
            if response_id:
                events.append(RealtimeEvent("response_done", {"response_id": response_id, "status": "completed", "usage": self._last_usage}))
            self._active_turn = ""
            self.state = RealtimeEngineState.READY
        return events
