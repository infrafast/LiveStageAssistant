import base64
import json
import unittest

from voice_assistant.realtime.engine import (
    RealtimeEngineConfig,
    RealtimeEngineState,
    RealtimeEvent,
    RealtimeFunctionTool,
    RealtimeMCPServer,
)
from voice_assistant.realtime.openai_realtime import OpenAIRealtimeEngine


class OpenAIRealtimeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.engine = OpenAIRealtimeEngine(
            RealtimeEngineConfig(provider="openai", model="gpt-realtime-2.1-mini"),
            api_key="test-key",
        )

    def test_session_updated_moves_engine_ready(self):
        event = self.engine._translate_event({"type": "session.updated", "session": {"id": "sess"}})
        self.assertEqual(event.type, "ready")
        self.assertEqual(self.engine.state, RealtimeEngineState.READY)

    def test_audio_delta_decodes_pcm_and_preserves_response_id(self):
        pcm = b"\x01\x02\x03\x04"
        event = self.engine._translate_event(
            {
                "type": "response.output_audio.delta",
                "response_id": "resp_1",
                "delta": base64.b64encode(pcm).decode("ascii"),
            }
        )
        self.assertEqual(event.type, "audio_delta")
        self.assertEqual(event.data["audio"], pcm)
        self.assertEqual(event.data["response_id"], "resp_1")

    def test_response_done_exposes_native_usage(self):
        usage = {"input_tokens": 10, "output_tokens": 20}
        event = self.engine._translate_event(
            {
                "type": "response.done",
                "response": {"id": "resp_2", "status": "completed", "usage": usage},
            }
        )
        self.assertEqual(event.type, "response_done")
        self.assertEqual(event.data["response_id"], "resp_2")
        self.assertEqual(event.data["usage"], usage)
        self.assertEqual(self.engine.state, RealtimeEngineState.READY)

    def test_native_mcp_defaults_are_permissive(self):
        server = RealtimeMCPServer(label="mixer", url="https://mixer.example.test/mcp")
        engine = OpenAIRealtimeEngine(
            RealtimeEngineConfig(
                provider="openai",
                model="gpt-realtime-2.1-mini",
                mcp_servers=(server,),
            ),
            api_key="test-key",
        )
        self.assertEqual(server.require_approval, "never")
        self.assertEqual(server.allowed_tools, ())
        self.assertEqual(
            engine._session_tools(),
            [
                {
                    "type": "mcp",
                    "server_label": "mixer",
                    "server_url": "https://mixer.example.test/mcp",
                    "require_approval": "never",
                }
            ],
        )

    def test_session_tools_translates_native_mcp_server(self):
        engine = OpenAIRealtimeEngine(
            RealtimeEngineConfig(
                provider="openai",
                model="gpt-realtime-2.1-mini",
                mcp_servers=(
                    RealtimeMCPServer(
                        label="mixer",
                        url="https://mixer.example.test/mcp",
                        authorization="secret-token",
                        headers={"X-Test": "value"},
                        allowed_tools=("read_main", "read_channel"),
                        require_approval="never",
                        description="Stage mixer MCP",
                    ),
                ),
            ),
            api_key="test-key",
        )
        self.assertEqual(
            engine._session_tools(),
            [
                {
                    "type": "mcp",
                    "server_label": "mixer",
                    "server_url": "https://mixer.example.test/mcp",
                    "require_approval": "never",
                    "authorization": "secret-token",
                    "headers": {"X-Test": "value"},
                    "allowed_tools": ["read_main", "read_channel"],
                }
            ],
        )

    def test_session_tools_translates_bridge_functions_without_native_mcp(self):
        engine = OpenAIRealtimeEngine(
            RealtimeEngineConfig(
                provider="openai",
                model="gpt-realtime-2.1-mini",
                function_tools=(
                    RealtimeFunctionTool(
                        name="mcp__mixer__read_main",
                        description="Read main level",
                        parameters={"type": "object", "properties": {}},
                    ),
                ),
            ),
            api_key="test-key",
        )
        self.assertEqual(
            engine._session_tools(),
            [
                {
                    "type": "function",
                    "name": "mcp__mixer__read_main",
                    "description": "Read main level",
                    "parameters": {"type": "object", "properties": {}},
                }
            ],
        )
        self.assertFalse(any(tool.get("type") == "mcp" for tool in engine._session_tools()))

    def test_translates_function_call_arguments_done(self):
        event = self.engine._translate_event(
            {
                "type": "response.function_call_arguments.done",
                "response_id": "resp_bridge",
                "item_id": "item_bridge",
                "call_id": "call_123",
                "name": "mcp__mixer__read_main",
                "arguments": "{\"target\":\"main\"}",
            }
        )
        self.assertEqual(event.type, "tool_call")
        self.assertEqual(event.data["call_id"], "call_123")
        self.assertEqual(event.data["name"], "mcp__mixer__read_main")
        self.assertEqual(event.data["arguments"], "{\"target\":\"main\"}")
        self.assertEqual(event.data["response_id"], "resp_bridge")

    def test_translates_user_input_transcript(self):
        event = self.engine._translate_event(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "item_user_1",
                "transcript": "Monte la basse de deux dB.",
            }
        )
        self.assertEqual(event.type, "user_transcript_done")
        self.assertEqual(event.data["text"], "Monte la basse de deux dB.")
        self.assertEqual(event.data["item_id"], "item_user_1")

    def test_translates_mcp_list_tools_item(self):
        event = self.engine._translate_event(
            {
                "type": "response.output_item.done",
                "response_id": "resp_tools",
                "item": {"type": "mcp_list_tools", "id": "item_1", "server_label": "mixer", "tools": []},
            }
        )
        self.assertEqual(event.type, "mcp_list_tools")
        self.assertEqual(event.data["phase"], "done")
        self.assertEqual(event.data["item"]["server_label"], "mixer")
        self.assertEqual(event.data["response_id"], "resp_tools")

    def test_translates_mcp_call_item(self):
        event = self.engine._translate_event(
            {
                "type": "response.output_item.added",
                "response_id": "resp_call",
                "item": {
                    "type": "mcp_call",
                    "id": "item_2",
                    "server_label": "mixer",
                    "name": "read_main",
                    "arguments": "{}",
                },
            }
        )
        self.assertEqual(event.type, "mcp_call")
        self.assertEqual(event.data["phase"], "added")
        self.assertEqual(event.data["item"]["name"], "read_main")
        self.assertEqual(event.data["response_id"], "resp_call")


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(json.loads(payload))


class OpenAIRealtimeNativeMCPFollowupTests(unittest.IsolatedAsyncioTestCase):
    def make_engine(self):
        engine = OpenAIRealtimeEngine(
            RealtimeEngineConfig(provider="openai", model="gpt-realtime-2.1-mini"),
            api_key="test-key",
        )
        engine._ws = FakeWebSocket()
        return engine

    async def test_completed_mcp_call_after_response_done_requests_one_followup(self):
        engine = self.make_engine()
        engine._response_active = False
        await engine._maybe_continue_after_native_mcp(
            RealtimeEvent("mcp_call", {"phase": "done", "response_id": "resp_1"})
        )
        self.assertEqual(engine._ws.sent, [{"type": "response.create"}])
        followup = await engine.next_event()
        self.assertEqual(followup.type, "mcp_followup_requested")

    async def test_completed_mcp_call_while_response_active_waits_for_response_done(self):
        engine = self.make_engine()
        engine._response_active = True
        await engine._maybe_continue_after_native_mcp(
            RealtimeEvent("mcp_call", {"phase": "done", "response_id": "resp_2"})
        )
        self.assertEqual(engine._ws.sent, [])
        done = engine._translate_event(
            {"type": "response.done", "response": {"id": "resp_2", "status": "completed", "usage": {}}}
        )
        await engine._maybe_continue_after_native_mcp(done)
        self.assertEqual(engine._ws.sent, [{"type": "response.create"}])

    async def test_cancelled_response_suppresses_late_native_mcp_followup(self):
        engine = self.make_engine()
        done = engine._translate_event(
            {"type": "response.done", "response": {"id": "resp_cancel", "status": "cancelled", "usage": {}}}
        )
        await engine._maybe_continue_after_native_mcp(done)
        await engine._maybe_continue_after_native_mcp(
            RealtimeEvent("mcp_call", {"phase": "done", "response_id": "resp_cancel"})
        )
        self.assertEqual(engine._ws.sent, [])


if __name__ == "__main__":
    unittest.main()
