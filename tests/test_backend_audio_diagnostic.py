import threading
import base64
import io
import wave

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
    min_speech_ms = 120
    chunk_ms = 32.0

    def __init__(self, speech: bool, *, threshold: float = 0.5):
        self.speech = speech
        self.threshold = threshold

    def reset(self) -> None:
        pass

    def process_pcm(self, _audio_data: bytes) -> list[float]:
        return [0.9, 0.9] if self.speech else [0.0, 0.0]


def build_assistant(
    frame: bytes,
    *,
    speech: bool,
    monkeypatch,
    configured_threshold: float = 0.5,
) -> agent.VoiceAssistant:
    assistant = agent.VoiceAssistant.__new__(agent.VoiceAssistant)
    assistant.backend_audio_capture_lock = threading.Lock()
    assistant.backend_audio_diagnostic_lock = threading.Lock()
    assistant.backend_audio_diagnostic_requested = threading.Event()
    assistant.backend_speaker_capture_stop_event = threading.Event()
    assistant.vad = FakeVad(speech, threshold=configured_threshold)
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


def speech_frame(amplitude: int, *, peak: int = 721) -> bytes:
    """Build speech with a controlled RMS and a realistic stronger transient peak."""
    samples = (np.sin(np.linspace(0, np.pi * 16, 1024)) * amplitude).astype(np.int16)
    samples[0] = peak
    return samples.tobytes()


def test_backend_audio_diagnostic_reports_silence(monkeypatch) -> None:
    frame = np.zeros(1024, dtype=np.int16).tobytes()
    assistant = build_assistant(frame, speech=False, monkeypatch=monkeypatch)

    result = assistant.diagnose_backend_audio_input("0", duration_seconds=3)

    assert result["verdict"] == "red"
    assert result["issues"] == ["no_signal"]


def test_backend_speaker_capture_returns_wav(monkeypatch) -> None:
    samples = (np.sin(np.linspace(0, np.pi * 16, 1024)) * 4000).astype(np.int16)
    assistant = build_assistant(samples.tobytes(), speech=True, monkeypatch=monkeypatch)

    result = assistant.capture_backend_speaker_sample("0", duration_seconds=3)

    wav_bytes = base64.b64decode(result["audio_base64"])
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getframerate() == 16000
        assert wav_file.getnframes() > 0
    assert result["ok"] is True
    assert result["duration_seconds"] >= 3.0


def test_backend_audio_diagnostic_accepts_clear_speech(monkeypatch) -> None:
    samples = (np.sin(np.linspace(0, np.pi * 16, 1024)) * 4000).astype(np.int16)
    assistant = build_assistant(samples.tobytes(), speech=True, monkeypatch=monkeypatch)

    result = assistant.diagnose_backend_audio_input("0", duration_seconds=3)

    assert result["verdict"] == "green"
    assert result["issues"] == ["good"]
    assert result["configured_vad"]["accepted"] is True
    assert result["metrics"]["speech_duration_seconds"] > 0
    assert result["audio_data_url"].startswith("data:audio/wav;base64,UklGR")


def test_backend_audio_diagnostic_accepts_audible_minus_36_dbfs(monkeypatch) -> None:
    assistant = build_assistant(speech_frame(700), speech=True, monkeypatch=monkeypatch)

    result = assistant.diagnose_backend_audio_input("0", duration_seconds=3)

    assert result["metrics"]["speech_rms_dbfs"] == -36.4
    assert result["metrics"]["peak_dbfs"] == -33.1
    assert result["verdict"] == "green"
    assert result["issues"] == ["good"]


def test_backend_audio_diagnostic_marks_minus_56_dbfs_orange(monkeypatch) -> None:
    assistant = build_assistant(speech_frame(67), speech=True, monkeypatch=monkeypatch)

    result = assistant.diagnose_backend_audio_input("0", duration_seconds=3)

    assert result["metrics"]["speech_rms_dbfs"] == -56.0
    assert result["metrics"]["peak_dbfs"] == -33.1
    assert result["verdict"] == "orange"
    assert result["issues"] == ["low"]


def test_backend_audio_diagnostic_marks_below_minus_56_dbfs_red(monkeypatch) -> None:
    assistant = build_assistant(speech_frame(65), speech=True, monkeypatch=monkeypatch)

    result = assistant.diagnose_backend_audio_input("0", duration_seconds=3)

    assert result["metrics"]["speech_rms_dbfs"] == -56.2
    assert result["verdict"] == "red"
    assert result["issues"] == ["very_low"]


def test_backend_audio_diagnostic_reports_severe_clipping(monkeypatch) -> None:
    frame = np.full(1024, 32767, dtype=np.int16).tobytes()
    assistant = build_assistant(frame, speech=True, monkeypatch=monkeypatch)

    result = assistant.diagnose_backend_audio_input("0", duration_seconds=3)

    assert result["verdict"] == "red"
    assert "severe_clipping" in result["issues"]


def test_strict_configured_vad_does_not_change_hardware_verdict(monkeypatch) -> None:
    samples = (np.sin(np.linspace(0, np.pi * 16, 1024)) * 4000).astype(np.int16)
    assistant = build_assistant(
        samples.tobytes(),
        speech=True,
        monkeypatch=monkeypatch,
        configured_threshold=0.95,
    )

    result = assistant.diagnose_backend_audio_input("0", duration_seconds=3)

    assert result["verdict"] == "green"
    assert result["issues"] == ["good"]
    assert result["configured_vad"]["accepted"] is False
