import sys
import types

import numpy as np

from voice_assistant.agent import (
    BackendWakeWordDetector,
    format_backend_listening_message,
    normalize_backend_wake_word_mode,
    parse_env_list,
)


def install_fake_openwakeword(monkeypatch, scores):
    class FakeModel:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def predict(self, _samples):
            score = scores.pop(0) if scores else 0.0
            return {"regie": score}

    package = types.ModuleType("openwakeword")
    model_module = types.ModuleType("openwakeword.model")
    model_module.Model = FakeModel
    monkeypatch.setitem(sys.modules, "openwakeword", package)
    monkeypatch.setitem(sys.modules, "openwakeword.model", model_module)


def test_backend_wake_word_detector_buffers_80ms_frames(monkeypatch):
    install_fake_openwakeword(monkeypatch, [0.8])
    detector = BackendWakeWordDetector(
        model_paths=["regie.onnx"],
        model_names=[],
        threshold=0.5,
        cooldown_ms=0,
    )

    partial_frame = np.zeros(640, dtype=np.int16).tobytes()
    full_frame_tail = np.zeros(640, dtype=np.int16).tobytes()

    assert detector.process_pcm16_16k(partial_frame) is None
    assert detector.process_pcm16_16k(full_frame_tail) == ("regie", 0.8)
    assert detector.model.kwargs["inference_framework"] == "onnx"
    assert detector.model.kwargs["wakeword_models"] == ["regie.onnx"]


def test_backend_wake_word_detector_uses_threshold(monkeypatch):
    install_fake_openwakeword(monkeypatch, [0.49, 0.51])
    detector = BackendWakeWordDetector(
        model_paths=["regie.onnx"],
        model_names=[],
        threshold=0.5,
        cooldown_ms=0,
    )
    frame = np.zeros(1280, dtype=np.int16).tobytes()

    assert detector.process_pcm16_16k(frame) is None
    assert detector.process_pcm16_16k(frame) == ("regie", 0.51)


def test_backend_wake_word_config_helpers():
    assert normalize_backend_wake_word_mode("streaming") == "openwakeword"
    assert normalize_backend_wake_word_mode("post-stt") == "post_stt"
    assert normalize_backend_wake_word_mode("unknown") == "post_stt"
    assert parse_env_list("a.onnx, b.onnx;c.onnx| d.onnx") == [
        "a.onnx",
        "b.onnx",
        "c.onnx",
        "d.onnx",
    ]
    assert format_backend_listening_message(["momo"], "openwakeword") == 'Listening for "momo" using openwakeword...'
    assert (
        format_backend_listening_message(["régie", "console"], "generic")
        == 'Listening for "régie, console" using generic...'
    )
    assert format_backend_listening_message([], None) == "Listening (no wake word)"
