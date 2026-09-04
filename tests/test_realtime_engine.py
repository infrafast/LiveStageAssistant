import asyncio
import unittest

from voice_assistant.realtime.engine import RealtimeEngine, RealtimeEngineConfig, RealtimeEngineState, RealtimeEvent


class DummyEngine(RealtimeEngine):
    def __init__(self, config):
        super().__init__(config)
        self.events = asyncio.Queue()

    async def start(self):
        self.state = RealtimeEngineState.READY

    async def stop(self):
        self.state = RealtimeEngineState.STOPPED

    async def send_audio(self, pcm: bytes):
        return None

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

    def test_config_requires_provider_model_and_voice(self):
        with self.assertRaises(ValueError):
            RealtimeEngineConfig(provider="", model="x")
        with self.assertRaises(ValueError):
            RealtimeEngineConfig(provider="x", model="")
        with self.assertRaises(ValueError):
            RealtimeEngineConfig(provider="x", model="y", voice="")


if __name__ == "__main__":
    unittest.main()
