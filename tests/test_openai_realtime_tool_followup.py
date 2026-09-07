import json
import unittest

from voice_assistant.realtime.engine import RealtimeEngineConfig
from voice_assistant.realtime.openai_realtime import OpenAIRealtimeEngine


class DummyWebSocket:
    def __init__(self):
        self.payloads = []

    async def send(self, raw):
        self.payloads.append(json.loads(raw))


class OpenAIRealtimeToolFollowupTests(unittest.IsolatedAsyncioTestCase):
    def build_engine(self):
        engine = OpenAIRealtimeEngine(
            RealtimeEngineConfig(provider="openai", model="gpt-realtime-2.1"),
            api_key="test-key",
        )
        engine._ws = DummyWebSocket()
        return engine

    async def test_tool_result_waits_for_active_response_to_finish(self):
        engine = self.build_engine()
        engine._response_active = True

        await engine.submit_tool_result("call-1", {"ok": True})

        self.assertEqual(engine._ws.payloads[0]["type"], "conversation.item.create")
        self.assertEqual(engine._ws.payloads[0]["item"]["type"], "function_call_output")
        self.assertNotIn("response.create", [item["type"] for item in engine._ws.payloads])
        self.assertTrue(engine._function_followup_pending)

        done = engine._translate_event({
            "type": "response.done",
            "response": {"id": "resp-1", "status": "completed", "usage": {}},
        })
        await engine._maybe_continue_after_tools(done)

        self.assertEqual(engine._ws.payloads[-1]["type"], "response.create")
        self.assertFalse(engine._function_followup_pending)

    async def test_tool_result_starts_followup_immediately_when_idle(self):
        engine = self.build_engine()
        engine._response_active = False

        await engine.submit_tool_result("call-2", "done")

        self.assertEqual(
            [item["type"] for item in engine._ws.payloads],
            ["conversation.item.create", "response.create"],
        )
        self.assertFalse(engine._function_followup_pending)

    async def test_cancelled_response_drops_pending_followup(self):
        engine = self.build_engine()
        engine._response_active = True
        await engine.submit_tool_result("call-3", {"ok": True})

        cancelled = engine._translate_event({
            "type": "response.done",
            "response": {"id": "resp-3", "status": "cancelled", "usage": {}},
        })
        await engine._maybe_continue_after_tools(cancelled)

        self.assertFalse(engine._function_followup_pending)
        self.assertNotEqual(engine._ws.payloads[-1]["type"], "response.create")


if __name__ == "__main__":
    unittest.main()
