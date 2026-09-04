import unittest

from voice_assistant.realtime import RealtimeEngine, RealtimeEngineConfig, RealtimeEngineState


class DummyEngine(RealtimeEngine):
    async def start(self):
        self.state = RealtimeEngineState.READY

    async def stop(self):
        self.state = RealtimeEngineState.STOPPED

    async def send_audio(self, pcm: bytes):
        return None

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

    def test_config_requires_provider_and_model(self):
        with self.assertRaises(ValueError):
            RealtimeEngineConfig(provider="", model="x")
        with self.assertRaises(ValueError):
            RealtimeEngineConfig(provider="x", model="")


if __name__ == "__main__":
    unittest.main()
