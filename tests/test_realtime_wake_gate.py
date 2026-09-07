import unittest

import numpy as np

from voice_assistant.realtime.wake_gate import RealtimeWakeConfig, RealtimeWakeGate


class RealtimeWakeGateTests(unittest.TestCase):
    def test_disabled_gate_is_always_authorized(self):
        gate = RealtimeWakeGate(RealtimeWakeConfig())
        self.assertFalse(gate.enabled)
        self.assertTrue(gate.authorized)
        self.assertFalse(gate.waiting)
        self.assertFalse(gate.feed(b"\x00\x00" * 480))

    def test_detects_threshold_and_authorizes_once(self):
        calls = []

        def predictor(samples):
            calls.append(len(samples))
            return {"momo": 0.8}

        gate = RealtimeWakeGate(
            RealtimeWakeConfig(wake_word="momo", threshold=0.6),
            predictor=predictor,
        )
        pcm24k = np.zeros(1920, dtype=np.int16).tobytes()
        self.assertTrue(gate.waiting)
        self.assertTrue(gate.feed(pcm24k))
        self.assertTrue(gate.authorized)
        self.assertEqual(calls, [1280])
        self.assertFalse(gate.feed(pcm24k))

    def test_below_threshold_stays_waiting(self):
        gate = RealtimeWakeGate(
            RealtimeWakeConfig(wake_word="momo", threshold=0.6),
            predictor=lambda _samples: {"momo": 0.2},
        )
        pcm24k = np.zeros(1920, dtype=np.int16).tobytes()
        self.assertFalse(gate.feed(pcm24k))
        self.assertTrue(gate.waiting)

    def test_rearm_returns_to_wait_wake(self):
        gate = RealtimeWakeGate(
            RealtimeWakeConfig(wake_word="momo", threshold=0.6),
            predictor=lambda _samples: {"momo": 0.9},
        )
        pcm24k = np.zeros(1920, dtype=np.int16).tobytes()
        self.assertTrue(gate.feed(pcm24k))
        self.assertTrue(gate.authorized)
        gate.rearm()
        self.assertTrue(gate.waiting)

    def test_env_config_uses_existing_keys(self):
        config = RealtimeWakeConfig.from_env({
            "WAKE_WORD": "momo",
            "BACKEND_WAKE_WORD_MODEL_PATHS": "a.onnx,b.onnx",
            "BACKEND_WAKE_WORD_MODEL_NAMES": "hey_jarvis",
            "BACKEND_WAKE_WORD_THRESHOLD": "0.61",
            "BACKEND_WAKE_WORD_COOLDOWN_MS": "1500",
        })
        self.assertTrue(config.enabled)
        self.assertEqual(config.model_paths, ("a.onnx", "b.onnx"))
        self.assertEqual(config.model_names, ("hey_jarvis",))
        self.assertEqual(config.threshold, 0.61)
        self.assertEqual(config.cooldown_ms, 1500)


if __name__ == "__main__":
    unittest.main()
