from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import numpy as np

from voice_assistant import agent


class FakeWhisperModel:
    def __init__(self) -> None:
        self.audio = None
        self.kwargs = None

    def transcribe(self, audio, **kwargs):
        self.audio = audio
        self.kwargs = kwargs
        return iter([SimpleNamespace(text=" mets batterie à moins trente dB")]), SimpleNamespace()


def _bare_assistant(model: FakeWhisperModel):
    assistant = object.__new__(agent.VoiceAssistant)
    assistant.rate = 16000
    assistant.stt_language = "fr"
    assistant.stt_prompt = "Commandes audio en français."
    assistant.local_whisper_model_name = "small"
    assistant._load_local_whisper_model = lambda: model
    assistant._local_whisper_hotwords = lambda: "mets, monte, baisse"
    assistant.normalize_stt_command_text = lambda text: text.strip()
    return assistant


def test_local_whisper_uses_native_pcm_without_temp_wav():
    model = FakeWhisperModel()
    assistant = _bare_assistant(model)
    pcm = np.array([-32768, -16384, 0, 16384, 32767], dtype="<i2").tobytes()

    with mock.patch.object(agent.tempfile, "NamedTemporaryFile", side_effect=AssertionError("temp WAV should not be used")):
        text = agent.VoiceAssistant.audio_to_text_local_whisper(assistant, pcm)

    assert text == "mets batterie à moins trente dB"
    assert isinstance(model.audio, np.ndarray)
    assert model.audio.dtype == np.float32
    np.testing.assert_allclose(
        model.audio,
        np.array([-1.0, -0.5, 0.0, 0.5, 32767 / 32768.0], dtype=np.float32),
        rtol=0,
        atol=1e-7,
    )
    assert model.kwargs["language"] == "fr"
    assert model.kwargs["beam_size"] == 1
    assert model.kwargs["best_of"] == 1
    assert model.kwargs["temperature"] == 0.0
    assert model.kwargs["condition_on_previous_text"] is False
    assert model.kwargs["without_timestamps"] is True
    assert model.kwargs["vad_filter"] is False
    assert model.kwargs["max_new_tokens"] == 48


def test_local_whisper_hotwords_are_cached_after_first_build():
    assistant = object.__new__(agent.VoiceAssistant)
    calls = 0

    def routing_keywords():
        nonlocal calls
        calls += 1
        return ["mixer", "qlc"]

    assistant._mcp_routing_keywords = routing_keywords

    first = agent.VoiceAssistant._local_whisper_hotwords(assistant)
    second = agent.VoiceAssistant._local_whisper_hotwords(assistant)

    assert first == second
    assert calls == 1
    assert "mets" in first
    assert "mixer" in first
