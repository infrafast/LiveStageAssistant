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
