import asyncio
import unittest
from unittest.mock import patch

from voice_assistant.semantic_audio import SemanticAudioState
from voice_assistant.realtime.engine import (
    RealtimeEngine,
    RealtimeEngineConfig,
    RealtimeEngineState,
    RealtimeEvent,
    RealtimeFunctionTool,
    RealtimeMCPServer,
)
from voice_assistant.realtime import service as realtime_service
from voice_assistant.startup_messages import startup_ready_message


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

    def test_realtime_startup_ready_message_reports_tool_count(self):
        with patch.dict("os.environ", {"STT_LANGUAGE": "fr"}, clear=False):
            self.assertEqual(
                startup_ready_message(stt_language="fr", tool_count=95),
                "Assistant vocal prêt à exécuter des commandes, 95 outils disponibles !",
            )
            self.assertEqual(
                startup_ready_message(stt_language="fr", tool_count=0),
                "Assistant vocal prêt à exécuter des commandes, aucun MCP connecté.",
            )

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

    def test_config_composes_global_and_mcp_instructions_generically(self):
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
        self.assertNotIn("Validation runner prompt.", config.instructions)
        self.assertIn(mcp_instructions, config.instructions)
        self.assertIn("MCP-provided instructions follow", config.instructions)
        self.assertIn("must not add, infer, or hard-code domain-specific concepts", config.instructions)
        self.assertIn("Examples inside MCP instructions are illustrative only", config.instructions)
        self.assertIn("never copy an example's entity names, labels, values, indexes, destinations, sources", config.instructions)
        self.assertIn("Preserve the entities and intent of the current user request exactly", config.instructions)
        self.assertNotIn("Realtime voice rules", config.instructions)

    def test_native_mcp_context_is_composed_without_domain_knowledge(self):
        native_instructions = "Use only identifiers and operation semantics provided by this MCP server."
        server = RealtimeMCPServer(
            label="service-a",
            url="https://service.example.test/mcp",
            context_instructions=native_instructions,
        )
        with patch.dict("os.environ", {"ASSISTANT_SYSTEM_PROMPT": "Global LSA prompt."}, clear=False):
            config = RealtimeEngineConfig(
                provider="test",
                model="test-model",
                instructions="Native validation prompt.",
                mcp_servers=(server,),
            )
        self.assertIn(native_instructions, config.instructions)
        self.assertIn("Global LSA prompt.", config.instructions)
        self.assertNotIn("Native validation prompt.", config.instructions)
        self.assertIn("Examples inside MCP instructions are illustrative only", config.instructions)
        self.assertNotIn("Realtime voice rules", config.instructions)

    def test_turn_tracker_keeps_turn_busy_until_tool_followup_response(self):
        tracker = realtime_service.RealtimeTurnTracker(action_grace_seconds=0)
        tracker.start_text_turn()
        tracker.response_started_event("resp_1", 1.0)
        tracker.tool_started()
        tracker.response_done("resp_1")
        self.assertTrue(tracker.has_pending_work())

        tracker.tool_finished(expect_followup=True)
        self.assertTrue(tracker.has_pending_work())

        tracker.tool_followup_requested()
        self.assertTrue(tracker.has_pending_work())

        tracker.response_started_event("resp_2", 2.0)
        self.assertFalse(tracker.waiting_for_tool_followup_response)
        self.assertTrue(tracker.has_pending_work())
        tracker.response_done("resp_2")
        self.assertFalse(tracker.has_pending_work())

    def test_turn_tracker_does_not_wait_forever_after_tool_result_delivery_failure(self):
        tracker = realtime_service.RealtimeTurnTracker(action_grace_seconds=0)
        tracker.tool_started()
        tracker.tool_finished(expect_followup=False)
        self.assertFalse(tracker.has_pending_work())

    def test_response_cancel_not_active_is_benign_provider_error(self):
        self.assertTrue(
            realtime_service.is_benign_provider_error(
                {
                    "error": {
                        "type": "invalid_request_error",
                        "code": "response_cancel_not_active",
                        "message": "Cancellation failed: no active response found",
                    }
                }
            )
        )
        self.assertFalse(realtime_service.is_benign_provider_error({"error": {"code": "server_error"}}))

    async def test_settle_completed_response_defers_idle_when_tool_followup_is_pending(self):
        class Semantic:
            def __init__(self):
                self.states = []

            def transition(self, state):
                self.states.append(state)

        tracker = realtime_service.RealtimeTurnTracker(action_grace_seconds=0)
        tracker.tool_started()
        tracker.tool_finished(expect_followup=True)
        queue = asyncio.Queue()
        semantic = Semantic()

        with patch.dict("os.environ", {"REALTIME_TURN_SETTLE_SECONDS": "0"}, clear=False):
            await realtime_service.settle_completed_response(
                response_id="resp_1",
                response_had_audio=False,
                turn_tracker=tracker,
                tool_tasks=set(),
                queue=queue,
                semantic=semantic,
            )

        self.assertEqual(semantic.states, [])

    async def test_settle_completed_response_returns_to_listening_when_turn_is_stable(self):
        class Semantic:
            def __init__(self):
                self.states = []

            def transition(self, state):
                self.states.append(state)

        tracker = realtime_service.RealtimeTurnTracker(action_grace_seconds=0)
        queue = asyncio.Queue()
        semantic = Semantic()
        busy = []

        with patch.dict("os.environ", {"REALTIME_TURN_SETTLE_SECONDS": "0"}, clear=False):
            await realtime_service.settle_completed_response(
                response_id="resp_1",
                response_had_audio=False,
                turn_tracker=tracker,
                tool_tasks=set(),
                queue=queue,
                semantic=semantic,
                set_busy=busy.append,
            )

        self.assertEqual(semantic.states, [SemanticAudioState.IDLE, SemanticAudioState.LISTENING])
        self.assertEqual(busy, [False])


if __name__ == "__main__":
    unittest.main()
