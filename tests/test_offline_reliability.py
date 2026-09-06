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


def test_auto_network_status_offline_uses_piper(monkeypatch):
    calls = {}
    values = {
        "CONNECTIVITY_MODE": "offline",
        "PIPER_VOICE": "fr_FR-siwis-medium",
        "BACKEND_AUDIO_OUTPUT_DEVICE": "pipewire:sink:alsa_output.test",
    }

    monkeypatch.setattr(
        agent_module,
        "speak_local_status",
        lambda text, config: calls.update({"text": text, "values": config}) or True,
    )

    agent_module.speak_auto_network_status(
        "Assistant fonctionne localement",
        Path(".env.offline"),
        lambda _path: values,
    )

    assert calls["text"] == "Assistant fonctionne localement"
    assert calls["values"] == values


def test_offline_profiles_are_cloud_independent_and_use_implicit_piper():
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
        assert "TTS_PROVIDER" not in values
        assert not any(key.startswith("LOCAL_TTS_") for key in values)
        assert values["PIPER_VOICE"] == "fr_FR-siwis-medium"
        assert values["PIPER_DATA_DIR"] == "data/piper"
        assert values["WEB_TTS_PROVIDER"] == "none"
        assert values["OPENAI_API_KEY_FILE"] == ""
        assert values["ELEVENLABS_API_KEY_FILE"] == ""


def test_raspberry_service_has_bounded_shutdown():
    service = Path("raspi_service_pack_stdio/livestageassistant.service").read_text()
    assert "KillMode=control-group" in service
    assert "TimeoutStopSec=15" in service
