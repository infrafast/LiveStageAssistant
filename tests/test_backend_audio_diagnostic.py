import threading

import numpy as np

from voice_assistant import agent


class FakeStream:
    def __init__(self, frame: bytes):
        self.frame = frame

    def read(self, _chunk: int, exception_on_overflow: bool = False) -> bytes:
        return self.frame

    def is_active(self) -> bool:
        return True

    def stop_stream(self) -> None:
        pass

    def close(self) -> None:
        pass


class FakeAudio:
    def __init__(self, frame: bytes):
        self.frame = frame

    def open(self, **_kwargs) -> FakeStream:
        return FakeStream(self.frame)

    def get_sample_size(self, _audio_format: int) -> int:
        return 2

    def terminate(self) -> None:
        pass


class FakeVad:
    threshold = 0.5
    min_speech_ms = 120
    chunk_ms = 32.0

    def __init__(self, speech: bool):
        self.speech = speech

    def reset(self) -> None:
        pass

    def process_pcm(self, _audio_data: bytes) -> list[float]:
        return [0.9, 0.9] if self.speech else [0.0, 0.0]


def build_assistant(frame: bytes, *, speech: bool, monkeypatch) -> agent.VoiceAssistant:
    assistant = agent.VoiceAssistant.__new__(agent.VoiceAssistant)
    assistant.backend_audio_capture_lock = threading.Lock()
    assistant.backend_audio_diagnostic_lock = threading.Lock()
    assistant.backend_audio_diagnostic_requested = threading.Event()
    assistant.vad = FakeVad(speech)
    monkeypatch.setattr(agent.pyaudio, "PyAudio", lambda: FakeAudio(frame))
    monkeypatch.setattr(
        agent,
        "resolve_pyaudio_device_index",
        lambda *_args, **_kwargs: (0, "configured", "0: fake microphone"),
    )
    monkeypatch.setattr(
        agent,
        "resolve_backend_input_format",
        lambda *_args, **_kwargs: {
            "ok": True,
            "channels": 1,
            "rate": 16000,
            "chunk": 1024,
            "detail": "1ch/16000Hz",
        },
    )
    return assistant


def test_backend_audio_diagnostic_reports_silence(monkeypatch) -> None:
    frame = np.zeros(1024, dtype=np.int16).tobytes()
    assistant = build_assistant(frame, speech=False, monkeypatch=monkeypatch)

    result = assistant.diagnose_backend_audio_input("0", duration_seconds=3)

    assert result["verdict"] == "red"
    assert result["issues"] == ["no_signal"]


def test_backend_audio_diagnostic_accepts_clear_speech(monkeypatch) -> None:
    samples = (np.sin(np.linspace(0, np.pi * 16, 1024)) * 4000).astype(np.int16)
    assistant = build_assistant(samples.tobytes(), speech=True, monkeypatch=monkeypatch)

    result = assistant.diagnose_backend_audio_input("0", duration_seconds=3)

    assert result["verdict"] == "green"
    assert result["issues"] == ["good"]
    assert result["metrics"]["speech_duration_seconds"] > 0
    assert result["audio_data_url"].startswith("data:audio/wav;base64,UklGR")


def test_backend_audio_diagnostic_reports_severe_clipping(monkeypatch) -> None:
    frame = np.full(1024, 32767, dtype=np.int16).tobytes()
    assistant = build_assistant(frame, speech=True, monkeypatch=monkeypatch)

    result = assistant.diagnose_backend_audio_input("0", duration_seconds=3)

    assert result["verdict"] == "red"
    assert "severe_clipping" in result["issues"]
