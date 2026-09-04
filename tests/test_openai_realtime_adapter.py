import base64
import unittest

from voice_assistant.realtime.engine import RealtimeEngineConfig, RealtimeEngineState
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


if __name__ == "__main__":
    unittest.main()
