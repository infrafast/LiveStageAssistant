import asyncio
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

from voice_assistant.semantic_audio import SemanticAudioState
from voice_assistant.realtime import wake_runtime


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


class FakeResampler:
    def __init__(self, *_args):
        pass

    def process(self, pcm):
        return pcm


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

    def _service(self):
        controller_type = type("Controller", (), {"transition": FakeController.transition})
        return types.SimpleNamespace(
            SemanticAudioController=controller_type,
            Pcm16MonoResampler=FakeResampler,
            REALTIME_RATE=24000,
            downmix_pcm16=lambda pcm, _channels: pcm,
            capture_loop=None,
        )

    def test_listening_is_wait_wake_until_authorized(self):
        service = self._service()
        with mock.patch.object(wake_runtime, "RealtimeWakeGate", FakeGate):
            gate = wake_runtime.install(service, self._env_file())
        controller = service.SemanticAudioController()
        controller.states = []
        service.SemanticAudioController.transition(controller, SemanticAudioState.LISTENING)
        self.assertEqual(controller.states, [SemanticAudioState.WAIT_WAKE])
        gate.waiting = False
        service.SemanticAudioController.transition(controller, SemanticAudioState.LISTENING)
        self.assertEqual(controller.states[-1], SemanticAudioState.LISTENING)

    def test_speaking_rearms_and_idle_applies_post_tts_suppression(self):
        service = self._service()
        with mock.patch.object(wake_runtime, "RealtimeWakeGate", FakeGate):
            gate = wake_runtime.install(service, self._env_file())
        controller = service.SemanticAudioController()
        controller.states = []
        gate.waiting = False
        service.SemanticAudioController.transition(controller, SemanticAudioState.SPEAKING)
        self.assertEqual(gate.rearms[-1], 0)
        service.SemanticAudioController.transition(controller, SemanticAudioState.IDLE)
        self.assertIsNone(gate.rearms[-1])

    def test_capture_forwards_preroll_then_following_audio(self):
        service = self._service()
        with mock.patch.object(wake_runtime, "RealtimeWakeGate", FakeGate):
            wake_runtime.install(service, self._env_file())

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
            await service.capture_loop(engine, Stream(), 24000, 1, 480, stop_event)
            return engine

        engine = asyncio.run(scenario())
        self.assertEqual(engine.sent[0], b"PRE")
        self.assertEqual(len(engine.sent), 2)


if __name__ == "__main__":
    unittest.main()
