import asyncio
import unittest
from unittest.mock import patch

from voice_assistant.semantic_audio import SemanticAudioState
from voice_assistant.realtime.engine import RealtimeEngine, RealtimeEngineConfig, RealtimeEngineState, RealtimeEvent
from voice_assistant.realtime import service as realtime_service


class DummyEngine(RealtimeEngine):
    def __init__(self, config):
        super().__init__(config)
        self.events = asyncio.Queue()
        self.cancelled = 0

    async def start(self):
        self.state = RealtimeEngineState.READY

    async def stop(self):
        self.state = RealtimeEngineState.STOPPED

    async def send_audio(self, pcm: bytes):
        return None

    async def send_text(self, text: str, *, create_response: bool = True):
        return None

    async def create_response(self, *, instructions=None):
        return None

    async def commit_audio(self):
        return None

    async def next_event(self):
        return await self.events.get()

    async def cancel_response(self):
        self.cancelled += 1

    async def submit_tool_result(self, call_id: str, result):
        return None


class SlowResponseFeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_response_speaks_then_starts_processing(self):
        class Semantic:
            def __init__(self):
                self.states = []
                self.state = None

            def transition(self, state):
                self.states.append(state)
                self.state = state
                return True

        engine = DummyEngine(RealtimeEngineConfig(provider="test", model="test-model"))
        await engine.events.put(RealtimeEvent("speech_stopped", {}))
        semantic = Semantic()
        messages = []

        async def slow_feedback(message):
            messages.append(message)

            async def close_later():
                await asyncio.sleep(0.02)
                await engine.events.put(RealtimeEvent("connection_closed", {}))

            asyncio.create_task(close_later())
            return True

        callbacks = realtime_service.RealtimeRuntimeCallbacks(
            slow_response_feedback=slow_feedback,
        )

        with patch.dict(
            "os.environ",
            {
                "REALTIME_SLOW_RESPONSE_NOTICE_SECONDS": "0.01",
                "REALTIME_SLOW_RESPONSE_MESSAGE": "Connexion lente...",
                "REALTIME_INACTIVITY_TIMEOUT_SECONDS": "0",
            },
            clear=False,
        ):
            await realtime_service.event_loop(
                engine,
                None,
                asyncio.Queue(),
                set(),
                asyncio.Event(),
                semantic,
                asyncio.Event(),
                callbacks,
            )

        self.assertEqual(messages, ["Connexion lente..."])
        self.assertIn(SemanticAudioState.PROCESSING, semantic.states)

    async def test_fast_response_cancels_slow_feedback(self):
        class Semantic:
            def __init__(self):
                self.states = []
                self.state = None

            def transition(self, state):
                self.states.append(state)
                self.state = state
                return True

        engine = DummyEngine(RealtimeEngineConfig(provider="test", model="test-model"))
        await engine.events.put(RealtimeEvent("speech_stopped", {}))
        await engine.events.put(RealtimeEvent("response_started", {"response": {"id": "resp_1"}}))
        await engine.events.put(RealtimeEvent("connection_closed", {}))
        semantic = Semantic()
        messages = []

        async def slow_feedback(message):
            messages.append(message)
            return True

        callbacks = realtime_service.RealtimeRuntimeCallbacks(
            slow_response_feedback=slow_feedback,
        )

        with patch.dict(
            "os.environ",
            {
                "REALTIME_SLOW_RESPONSE_NOTICE_SECONDS": "0.05",
                "REALTIME_INACTIVITY_TIMEOUT_SECONDS": "0",
            },
            clear=False,
        ):
            await realtime_service.event_loop(
                engine,
                None,
                asyncio.Queue(),
                set(),
                asyncio.Event(),
                semantic,
                asyncio.Event(),
                callbacks,
            )

        self.assertEqual(messages, [])
        self.assertIn(SemanticAudioState.PROCESSING, semantic.states)



    async def test_timeout_recovery_is_bounded_and_rearms_listening(self):
        class Semantic:
            def __init__(self):
                self.states = []
                self.state = None

            def transition(self, state):
                self.states.append(state)
                self.state = state
                return True

        class HangingEngine(DummyEngine):
            async def cancel_response(self):
                await asyncio.Event().wait()

            async def discard_input_audio(self):
                await asyncio.Event().wait()

        engine = HangingEngine(RealtimeEngineConfig(provider="test", model="test-model"))
        tracker = realtime_service.RealtimeTurnTracker(action_grace_seconds=0)
        tracker.speech_started()
        semantic = Semantic()
        messages = []

        async def feedback(message):
            messages.append(message)
            return True

        callbacks = realtime_service.RealtimeRuntimeCallbacks(
            slow_response_feedback=feedback,
        )

        with patch.dict(
            "os.environ",
            {
                "REALTIME_RECOVERY_OPERATION_TIMEOUT_SECONDS": "0.01",
                "REALTIME_TIMEOUT_MESSAGE": "Temps écoulé.",
            },
            clear=False,
        ):
            await asyncio.wait_for(
                realtime_service.recover_turn_timeout(
                    engine=engine,
                    turn_tracker=tracker,
                    interrupted=set(),
                    queue=asyncio.Queue(),
                    semantic=semantic,
                    callbacks=callbacks,
                    timeout_seconds=15.0,
                ),
                timeout=0.6,
            )

        self.assertEqual(messages, ["Temps écoulé."])
        self.assertEqual(
            semantic.states[-2:],
            [SemanticAudioState.IDLE, SemanticAudioState.LISTENING],
        )
        self.assertEqual(tracker.phase, realtime_service.RealtimeTurnPhase.IDLE)


if __name__ == "__main__":
    unittest.main()
