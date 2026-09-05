import asyncio
import unittest
from unittest.mock import patch

from voice_assistant.realtime.engine import (
    RealtimeEngine,
    RealtimeEngineConfig,
    RealtimeEngineState,
    RealtimeEvent,
    RealtimeFunctionTool,
    RealtimeMCPServer,
)


class DummyEngine(RealtimeEngine):
    def __init__(self, config):
        super().__init__(config)
        self.events = asyncio.Queue()
        self.text_turns = []

    async def start(self):
        self.state = RealtimeEngineState.READY

    async def stop(self):
        self.state = RealtimeEngineState.STOPPED

    async def send_audio(self, pcm: bytes):
        return None

    async def send_text(self, text: str, *, create_response: bool = True):
        self.text_turns.append((text, create_response))

    async def commit_audio(self):
        return None

    async def next_event(self):
        return await self.events.get()

    async def cancel_response(self):
        return None

    async def submit_tool_result(self, call_id: str, result):
        return None


class RealtimeEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_neutral_lifecycle_contract(self):
        engine = DummyEngine(RealtimeEngineConfig(provider="test", model="test-model"))
        self.assertEqual(engine.state, RealtimeEngineState.STOPPED)
        await engine.start()
        self.assertEqual(engine.state, RealtimeEngineState.READY)
        await engine.stop()
        self.assertEqual(engine.state, RealtimeEngineState.STOPPED)

    async def test_provider_neutral_event_contract(self):
        engine = DummyEngine(RealtimeEngineConfig(provider="test", model="test-model"))
        await engine.events.put(RealtimeEvent("audio_delta", {"audio": b"abc"}))
        event = await engine.next_event()
        self.assertEqual(event.type, "audio_delta")
        self.assertEqual(event.data["audio"], b"abc")

    async def test_provider_neutral_text_replay_contract(self):
        engine = DummyEngine(RealtimeEngineConfig(provider="test", model="test-model"))
        await engine.send_text("repeat this", create_response=True)
        self.assertEqual(engine.text_turns, [("repeat this", True)])

    def test_config_requires_provider_model_and_voice(self):
        with self.assertRaises(ValueError):
            RealtimeEngineConfig(provider="", model="x")
        with self.assertRaises(ValueError):
            RealtimeEngineConfig(provider="x", model="")
        with self.assertRaises(ValueError):
            RealtimeEngineConfig(provider="x", model="y", voice="")

    def test_native_mcp_server_is_provider_neutral_config(self):
        server = RealtimeMCPServer(
            label="service-a",
            url="https://service.example.test/mcp",
            authorization="token",
            headers={"X-Test": "value"},
            allowed_tools=("read_resource",),
            require_approval="never",
        )
        config = RealtimeEngineConfig(provider="test", model="test-model", mcp_servers=(server,))
        self.assertEqual(config.mcp_servers, (server,))
        self.assertEqual(config.mcp_servers[0].allowed_tools, ("read_resource",))

    def test_native_mcp_server_requires_label_url_and_valid_approval(self):
        with self.assertRaises(ValueError):
            RealtimeMCPServer(label="", url="https://example.test/mcp")
        with self.assertRaises(ValueError):
            RealtimeMCPServer(label="service-a", url="")
        with self.assertRaises(ValueError):
            RealtimeMCPServer(label="service-a", url="https://example.test/mcp", require_approval="sometimes")

    def test_config_composes_global_mcp_and_voice_instructions_generically(self):
        mcp_instructions = "Use the resource identifier returned by the discovery operation exactly as provided."
        tool = RealtimeFunctionTool(
            name="mcp__service_a__read_resource",
            context_instructions=mcp_instructions,
        )
        with patch.dict("os.environ", {"ASSISTANT_SYSTEM_PROMPT": "Global LSA prompt."}, clear=False):
            config = RealtimeEngineConfig(
                provider="test",
                model="test-model",
                instructions="Validation runner prompt.",
                function_tools=(tool,),
            )
        self.assertIn("Global LSA prompt.", config.instructions)
        self.assertIn("Validation runner prompt.", config.instructions)
        self.assertIn(mcp_instructions, config.instructions)
        self.assertIn("MCP-provided instructions follow", config.instructions)
        self.assertIn("must not add, infer, or hard-code domain-specific concepts", config.instructions)
        self.assertIn("Do not offer extra help", config.instructions)
        self.assertIn("one short confirmation sentence", config.instructions)


if __name__ == "__main__":
    unittest.main()
