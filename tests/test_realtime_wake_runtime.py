import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from voice_assistant.semantic_audio import SemanticAudioState
from voice_assistant.realtime import service as realtime_service
from voice_assistant.realtime import wake_runtime
from voice_assistant.realtime.wake_gate import RealtimeWakeConfig


class FakeController:
    def __init__(self):
        self.states = []

    def transition(self, state):
        self.states.append(state)
        return True


class FakeGate:
    def __init__(self, config):
        self.config = config
        self.waiting = True
        self.enabled = True
        self.rearms = []
        self.preroll = b"PRE"
        self.last_detection_label = "momo"
        self.last_detection_score = 0.9

    def feed(self, _pcm):
        self.waiting = False
        return True

    def consume_pre_roll(self):
        payload = self.preroll
        self.preroll = b""
        return payload

    def rearm(self, *, suppress_ms=None):
        self.waiting = True
        self.rearms.append(suppress_ms)
        self.preroll = b""


class RealtimeWakeRuntimeTests(unittest.TestCase):
    def _env_file(self, *, interrupt=False):
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False)
        handle.write("WAKE_WORD=momo\n")
        handle.write("BACKEND_WAKE_WORD_MODEL_NAMES=momo\n")
        handle.write("BACKEND_WAKE_WORD_THRESHOLD=0.60\n")
        handle.write("BACKEND_WAKE_WORD_PRE_ROLL_MS=1600\n")
        handle.write(f"INTERRUPT_CONVERSATION_ENABLED={'true' if interrupt else 'false'}\n")
        handle.close()
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
        return handle.name

    def _config(self):
        return RealtimeWakeConfig(
            wake_word="momo",
            model_names=("momo",),
            threshold=0.6,
            pre_roll_ms=1600,
        )

    async def _feed_local_command_end(self, runtime, engine):
        stop_event = asyncio.Event()
        # First frame authorizes the wake gate.
        await runtime.capture_filter(engine, b"wake", stop_event)
        # Then provide post-wake command speech followed by local silence.
        speech = (int(12000).to_bytes(2, "little", signed=True)) * 480
        silence = b"\x00\x00" * 480
        await runtime.capture_filter(engine, speech, stop_event)
        await runtime.capture_filter(engine, silence, stop_event)

    def test_listening_is_wait_wake_until_authorized(self):
        with mock.patch.object(wake_runtime, "RealtimeWakeGate", FakeGate):
            runtime = wake_runtime.RealtimeWakeRuntime(self._config())
        controller = FakeController()
        runtime.semantic_transition(controller, SemanticAudioState.LISTENING)
        self.assertEqual(controller.states, [SemanticAudioState.WAIT_WAKE])
        runtime.gate.waiting = False
        runtime.semantic_transition(controller, SemanticAudioState.LISTENING)
        self.assertEqual(controller.states[-1], SemanticAudioState.LISTENING)

    def test_speaking_rearms_and_idle_applies_post_tts_suppression(self):
        with mock.patch.object(wake_runtime, "RealtimeWakeGate", FakeGate):
            runtime = wake_runtime.RealtimeWakeRuntime(self._config())
        controller = FakeController()
        runtime.gate.waiting = False
        runtime.semantic_transition(controller, SemanticAudioState.SPEAKING)
        self.assertEqual(runtime.gate.rearms[-1], 0)
        runtime.semantic_transition(controller, SemanticAudioState.IDLE)
        self.assertIsNone(runtime.gate.rearms[-1])

    def test_capture_forwards_preroll_then_following_audio(self):
        with mock.patch.object(wake_runtime, "RealtimeWakeGate", FakeGate):
            runtime = wake_runtime.RealtimeWakeRuntime(self._config())

        class Stream:
            def __init__(self):
                self.calls = 0
            def read(self, _frames, _overflow):
                self.calls += 1
                if self.calls > 2:
                    raise RuntimeError("stop")
                return b"\x00\x00" * 480

        class Engine:
            def __init__(self):
                self.sent = []
            async def send_audio(self, pcm):
                self.sent.append(pcm)

        async def scenario():
            stop_event = asyncio.Event()
            engine = Engine()
            await realtime_service.capture_loop(
                engine,
                Stream(),
                24000,
                1,
                480,
                stop_event,
                runtime.capture_filter,
            )
            return engine

        engine = asyncio.run(scenario())
        self.assertEqual(engine.sent[0], b"PRE")
        self.assertEqual(len(engine.sent), 2)

    def test_post_wake_watchdog_fires_without_provider_events(self):
        with mock.patch.object(wake_runtime, "RealtimeWakeGate", FakeGate):
            messages = []

            async def feedback(message):
                messages.append(message)
                return True

            runtime = wake_runtime.RealtimeWakeRuntime(
                self._config(),
                slow_response_feedback=feedback,
                slow_response_delay_seconds=0.01,
                slow_response_message="Connexion lente...",
                post_wake_vad_threshold=0.01,
                post_wake_vad_ignore_ms=0,
                post_wake_end_silence_ms=20,
            )
        controller = FakeController()
        runtime.semantic = controller

        class Engine:
            def __init__(self):
                self.sent = []
            async def send_audio(self, pcm):
                self.sent.append(pcm)

        async def scenario():
            engine = Engine()
            await self._feed_local_command_end(runtime, engine)
            await asyncio.sleep(0.03)

        asyncio.run(scenario())
        self.assertEqual(messages, ["Connexion lente..."])
        self.assertIn(SemanticAudioState.WAKE_DETECTED, controller.states)
        self.assertIn(SemanticAudioState.PROCESSING, controller.states)

    def test_slow_status_blocks_capture_and_starts_processing_after_piper(self):
        with mock.patch.object(wake_runtime, "RealtimeWakeGate", FakeGate):
            release = asyncio.Event()
            capture_results = []

            async def feedback(_message):
                runtime._local_status_speaking = True
                capture_results.append(await runtime.capture_filter(object(), b"pcm", asyncio.Event()))
                runtime._local_status_speaking = False
                release.set()
                return True

            runtime = wake_runtime.RealtimeWakeRuntime(
                self._config(),
                slow_response_feedback=feedback,
                slow_response_delay_seconds=0.01,
                slow_response_message="Connexion lente...",
                post_wake_vad_threshold=0.01,
                post_wake_vad_ignore_ms=0,
                post_wake_end_silence_ms=20,
            )

        controller = FakeController()
        runtime.semantic = controller

        class Engine:
            async def send_audio(self, _pcm):
                return None

        async def scenario():
            engine = Engine()
            await self._feed_local_command_end(runtime, engine)
            await asyncio.wait_for(release.wait(), timeout=0.2)
            await asyncio.sleep(0.01)

        asyncio.run(scenario())
        self.assertEqual(capture_results, [True])
        self.assertIn(SemanticAudioState.PROCESSING, controller.states)

    def test_post_wake_abort_announces_timeout_and_rearms(self):
        with mock.patch.object(wake_runtime, "RealtimeWakeGate", FakeGate):
            messages = []

            async def feedback(message):
                messages.append(message)
                return True

            runtime = wake_runtime.RealtimeWakeRuntime(
                self._config(),
                slow_response_feedback=feedback,
                slow_response_delay_seconds=0.01,
                slow_response_message="Connexion lente...",
                post_wake_vad_threshold=0.01,
                post_wake_vad_ignore_ms=0,
                post_wake_end_silence_ms=20,
                post_wake_abort_seconds=0.02,
                timeout_message="Temps écoulé, commande annulée.",
                recovery_operation_timeout_seconds=0.01,
            )

        controller = FakeController()
        runtime.semantic = controller

        class Engine:
            async def send_audio(self, _pcm):
                return None

            async def cancel_response(self):
                return None

            async def discard_input_audio(self):
                return None

        async def scenario():
            engine = Engine()
            await self._feed_local_command_end(runtime, engine)
            await asyncio.sleep(0.08)

        asyncio.run(scenario())
        self.assertEqual(
            messages,
            ["Connexion lente...", "Temps écoulé, commande annulée."],
        )
        self.assertIn(SemanticAudioState.PROCESSING, controller.states)
        self.assertIn(SemanticAudioState.IDLE, controller.states)
        self.assertEqual(controller.states[-1], SemanticAudioState.WAIT_WAKE)
        self.assertTrue(runtime.gate.waiting)

    def test_provider_progress_during_slow_notice_disarms_post_wake_abort(self):
        with mock.patch.object(wake_runtime, "RealtimeWakeGate", FakeGate):
            messages = []
            release = asyncio.Event()

            async def feedback(message):
                messages.append(message)
                if message == "Connexion lente...":
                    runtime.provider_progress()
                    release.set()
                return True

            runtime = wake_runtime.RealtimeWakeRuntime(
                self._config(),
                slow_response_feedback=feedback,
                slow_response_delay_seconds=0.01,
                slow_response_message="Connexion lente...",
                post_wake_vad_threshold=0.01,
                post_wake_vad_ignore_ms=0,
                post_wake_end_silence_ms=20,
                post_wake_abort_seconds=0.02,
                timeout_message="Temps écoulé, commande annulée.",
                recovery_operation_timeout_seconds=0.01,
            )

        controller = FakeController()
        runtime.semantic = controller

        class Engine:
            async def send_audio(self, _pcm):
                return None
            async def cancel_response(self):
                raise AssertionError("post-wake abort must be disarmed after provider progress")
            async def discard_input_audio(self):
                raise AssertionError("post-wake abort must be disarmed after provider progress")

        async def scenario():
            engine = Engine()
            await self._feed_local_command_end(runtime, engine)
            await asyncio.wait_for(release.wait(), timeout=0.2)
            await asyncio.sleep(0.06)

        asyncio.run(scenario())
        self.assertEqual(messages, ["Connexion lente..."])
        self.assertNotIn("Temps écoulé, commande annulée.", messages)

    def test_provider_progress_is_safe_without_running_event_loop(self):
        with mock.patch.object(wake_runtime, "RealtimeWakeGate", FakeGate):
            runtime = wake_runtime.RealtimeWakeRuntime(self._config())
        runtime.provider_progress()
        self.assertTrue(runtime._post_wake_provider_progress_seen)

    def test_build_runtime_callbacks_does_not_patch_service_module(self):
        original_capture = realtime_service.capture_loop
        original_event_loop = realtime_service.event_loop
        with mock.patch.object(wake_runtime, "RealtimeWakeGate", FakeGate):
            callbacks = wake_runtime.build_runtime_callbacks(self._env_file())
        self.assertIs(realtime_service.capture_loop, original_capture)
        self.assertIs(realtime_service.event_loop, original_event_loop)
        self.assertIsNotNone(callbacks.capture_filter)
        self.assertIsNotNone(callbacks.semantic_transition)


if __name__ == "__main__":
    unittest.main()
