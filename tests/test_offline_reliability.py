from pathlib import Path

import voice_assistant.agent as agent_module


class FakeAudio:
    def __init__(self):
        self.terminated = False

    def terminate(self):
        self.terminated = True


def test_release_reload_audio_guard_terminates_and_clears():
    first = FakeAudio()
    second = FakeAudio()
    agent_module.RELOAD_AUDIO_GUARD[:] = [first, second]

    assert agent_module.release_reload_audio_guard() == 2
    assert first.terminated is True
    assert second.terminated is True
    assert agent_module.RELOAD_AUDIO_GUARD == []


def test_auto_network_status_pyttsx3_uses_configured_backend_output(monkeypatch, tmp_path):
    rendered = tmp_path / "rendered.wav"
    calls = {}

    class FakeEngine:
        def save_to_file(self, text, path):
            calls["text"] = text
            calls["path"] = path

        def runAndWait(self):
            Path(calls["path"]).write_bytes(b"fake-wav")

    fake_audio = FakeAudio()
    monkeypatch.setattr(agent_module, "TTS_ENGINE", FakeEngine())
    monkeypatch.setattr(agent_module.pyaudio, "PyAudio", lambda: fake_audio)
    monkeypatch.setattr(
        agent_module,
        "resolve_pyaudio_device_index",
        lambda _audio, selected, input_device=False: (7, "ok", selected or "default"),
    )
    monkeypatch.setattr(agent_module, "parse_pipewire_id", lambda selected, kind: "alsa_output.test" if selected else None)
    monkeypatch.setattr(agent_module, "play_wav_file_backend", lambda _audio, path, **kwargs: calls.update({"played": path, **kwargs}))

    values = {
        "TTS_PROVIDER": "pyttsx3",
        "WEB_TTS_PROVIDER": "none",
        "BACKEND_AUDIO_OUTPUT_DEVICE": "pipewire:sink:alsa_output.test",
        "BACKEND_TTS_VOLUME": "0.8",
        "BACKEND_AUDIO_OUTPUT_PAN": "-0.2",
    }
    agent_module.speak_auto_network_status(
        "Assistant fonctionne localement",
        Path(".env.offline"),
        lambda _path: values,
    )

    assert calls["text"] == "Assistant fonctionne localement"
    assert calls["output_device_index"] == 7
    assert calls["pipewire_target"] == "alsa_output.test"
    assert calls["volume"] == 0.8
    assert calls["pan"] == -0.2
    assert fake_audio.terminated is True


def test_offline_profiles_are_cloud_independent_and_select_piper():
    for relative_path in [".env.offline", "raspi_service_pack_stdio/.env.offline"]:
        values = {}
        for raw_line in Path(relative_path).read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"')

        assert values["CONNECTIVITY_MODE"] == "offline"
        assert values["LLM_PROVIDER"] == "ollama"
        assert values["STT_PROVIDER"] == "local-whisper"
        assert values["STT_INPUT"] == "backend"
        # TTS_PROVIDER remains the legacy Classic route identifier during OR3;
        # LOCAL_TTS_PROVIDER is the actual local backend selection.
        assert values["TTS_PROVIDER"] == "pyttsx3"
        assert values["LOCAL_TTS_PROVIDER"] == "piper"
        assert values["PIPER_VOICE"] == "fr_FR-siwis-medium"
        assert values["PIPER_DATA_DIR"] == "data/piper"
        assert values["LOCAL_TTS_PYTTSX3_FALLBACK"] == "true"
        assert values["WEB_TTS_PROVIDER"] == "none"
        assert values["OPENAI_API_KEY_FILE"] == ""
        assert values["ELEVENLABS_API_KEY_FILE"] == ""


def test_raspberry_service_has_bounded_shutdown():
    service = Path("raspi_service_pack_stdio/livestageassistant.service").read_text()
    assert "KillMode=control-group" in service
    assert "TimeoutStopSec=15" in service
