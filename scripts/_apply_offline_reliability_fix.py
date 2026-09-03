from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
agent_path = ROOT / "voice_assistant" / "agent.py"
service_path = ROOT / "raspi_service_pack_stdio" / "livestageassistant.service"
arch_path = ROOT / "docs" / "ARCHITECTURE.md"
test_path = ROOT / "tests" / "test_offline_reliability.py"

agent = agent_path.read_text()

old_guard = '''RELOAD_AUDIO_GUARD: list[Any] = []
SESSION_LLM_SUMMARY_PROMPT = (
'''
new_guard = '''RELOAD_AUDIO_GUARD: list[Any] = []


def release_reload_audio_guard() -> int:
    """Terminate PyAudio instances retained only to survive a runtime profile reload."""
    retained = list(RELOAD_AUDIO_GUARD)
    RELOAD_AUDIO_GUARD.clear()
    released = 0
    for audio in retained:
        try:
            audio.terminate()
            released += 1
        except Exception as e:
            print(f"Deferred backend audio termination failed: {e}")
    if released:
        print(f"Released {released} deferred backend audio instance(s) after reload.")
    return released


SESSION_LLM_SUMMARY_PROMPT = (
'''
if old_guard not in agent:
    raise SystemExit("RELOAD_AUDIO_GUARD anchor not found")
agent = agent.replace(old_guard, new_guard, 1)

old_none = '''    with TTS_LOCK:
        if cloud_provider == "none":
            print(f"Auto network status: {text}")
            return

        if cloud_provider == "elevenlabs":
'''
new_none = '''    with TTS_LOCK:
        if cloud_provider == "none":
            print(f"Auto network status: {text}")
            return

        if cloud_provider == "pyttsx3":
            temp_path = None
            temp_audio = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
                    temp_path = temp_file.name
                TTS_ENGINE.save_to_file(prepare_text_for_tts(text), temp_path)
                TTS_ENGINE.runAndWait()
                if not temp_path or not os.path.exists(temp_path) or os.path.getsize(temp_path) <= 0:
                    raise RuntimeError("pyttsx3 did not render network status audio")
                with suppress_native_stderr():
                    temp_audio = pyaudio.PyAudio()
                output_device_index, output_status, output_detail = resolve_pyaudio_device_index(
                    temp_audio,
                    values.get("BACKEND_AUDIO_OUTPUT_DEVICE"),
                    input_device=False,
                )
                if output_status in {"invalid", "unavailable"}:
                    raise RuntimeError(output_detail)
                pipewire_target = parse_pipewire_id(values.get("BACKEND_AUDIO_OUTPUT_DEVICE"), kind="sink")
                try:
                    play_wav_file_backend(
                        temp_audio,
                        temp_path,
                        output_device_index=output_device_index,
                        pipewire_target=pipewire_target,
                        volume=backend_tts_volume,
                        pan=backend_audio_output_pan,
                    )
                except Exception:
                    pcm_bytes = decode_audio_file_to_pcm_bytes(temp_path)
                    play_pcm_bytes(
                        temp_audio,
                        pcm_bytes,
                        sample_rate=DEFAULT_BACKEND_MP3_SAMPLE_RATE,
                        channels=DEFAULT_BACKEND_MP3_CHANNELS,
                        output_device_index=output_device_index,
                        pipewire_target=pipewire_target,
                        volume=backend_tts_volume,
                        pan=backend_audio_output_pan,
                    )
                return
            except Exception as e:
                print(f"Auto network status local pyttsx3 TTS failed: {e}")
                return
            finally:
                if temp_audio is not None:
                    try:
                        temp_audio.terminate()
                    except Exception:
                        pass
                if temp_path:
                    with contextlib.suppress(OSError):
                        os.unlink(temp_path)

        if cloud_provider == "elevenlabs":
'''
if old_none not in agent:
    raise SystemExit("network TTS anchor not found")
agent = agent.replace(old_none, new_none, 1)

old_build_manual = '''                assistant = build_assistant_from_env(active_env_file, reload_event=reload_event, web_monitor=web_monitor)
                reload_complete_message = None
'''
new_build_manual = '''                assistant = build_assistant_from_env(active_env_file, reload_event=reload_event, web_monitor=web_monitor)
                release_reload_audio_guard()
                reload_complete_message = None
'''
if old_build_manual not in agent:
    raise SystemExit("manual build anchor not found")
agent = agent.replace(old_build_manual, new_build_manual, 1)

old_build_auto = '''            assistant = build_assistant_from_env(detected_env_file, reload_event=reload_event, web_monitor=web_monitor)
            if announce_initial_network_status:
'''
new_build_auto = '''            assistant = build_assistant_from_env(detected_env_file, reload_event=reload_event, web_monitor=web_monitor)
            release_reload_audio_guard()
            if announce_initial_network_status:
'''
if old_build_auto not in agent:
    raise SystemExit("auto build anchor not found")
agent = agent.replace(old_build_auto, new_build_auto, 1)

agent_path.write_text(agent)

service = service_path.read_text()
old_service = '''Restart=always
RestartSec=5
KillMode=control-group
'''
new_service = '''Restart=always
RestartSec=5
KillMode=control-group
TimeoutStopSec=15
'''
if old_service not in service:
    raise SystemExit("systemd service anchor not found")
service_path.write_text(service.replace(old_service, new_service, 1))

arch = arch_path.read_text()
marker = '''## Remaining Wake Word And Audio Validation Work
'''
section = '''## Offline Reliability And Auto Profile Switching\n\nThe offline runtime remains intentionally cloud-independent: `.env.offline` uses Ollama for the LLM, faster-whisper for STT, pyttsx3 for backend TTS, and local/stdio MCP servers. Auto mode must preserve that contract in both transition directions.\n\nImplemented reliability rules:\n\n- Network-status announcements use the TTS provider from the newly detected profile. In particular, an online-to-offline transition with `TTS_PROVIDER=pyttsx3` renders local speech and plays it through the configured backend output instead of silently skipping the announcement.\n- Runtime profile reloads may defer termination of the outgoing PyAudio object while cleanup completes, but retained instances are released immediately after the replacement assistant has constructed its own audio stack. Repeated online/offline transitions must not accumulate stale PyAudio instances.\n- The Raspberry Pi systemd service uses `TimeoutStopSec=15` with `KillMode=control-group` so a pathological local TTS, Ollama, Whisper, or child MCP shutdown cannot block service stop indefinitely. Normal cleanup still gets the first opportunity to stop TTS and close MCP sessions cleanly.\n\nRegression coverage should keep checking that the offline profile does not require cloud API keys, that local network announcements target the configured backend output, and that deferred reload audio resources are terminated and cleared. Hardware recette should include online -> offline -> online transitions, a complete offline voice command, and `systemctl stop livestageassistant` while local processing/TTS is active.\n\n'''
if marker not in arch:
    raise SystemExit("architecture marker not found")
arch_path.write_text(arch.replace(marker, section + marker, 1))

test_path.write_text('''from pathlib import Path\n\nimport voice_assistant.agent as agent_module\n\n\nclass FakeAudio:\n    def __init__(self):\n        self.terminated = False\n\n    def terminate(self):\n        self.terminated = True\n\n\ndef test_release_reload_audio_guard_terminates_and_clears():\n    first = FakeAudio()\n    second = FakeAudio()\n    agent_module.RELOAD_AUDIO_GUARD[:] = [first, second]\n\n    assert agent_module.release_reload_audio_guard() == 2\n    assert first.terminated is True\n    assert second.terminated is True\n    assert agent_module.RELOAD_AUDIO_GUARD == []\n\n\ndef test_auto_network_status_pyttsx3_uses_configured_backend_output(monkeypatch, tmp_path):\n    rendered = tmp_path / "rendered.wav"\n    calls = {}\n\n    class FakeEngine:\n        def save_to_file(self, text, path):\n            calls["text"] = text\n            calls["path"] = path\n\n        def runAndWait(self):\n            Path(calls["path"]).write_bytes(b"fake-wav")\n\n    fake_audio = FakeAudio()\n    monkeypatch.setattr(agent_module, "TTS_ENGINE", FakeEngine())\n    monkeypatch.setattr(agent_module.pyaudio, "PyAudio", lambda: fake_audio)\n    monkeypatch.setattr(\n        agent_module,\n        "resolve_pyaudio_device_index",\n        lambda _audio, selected, input_device=False: (7, "ok", selected or "default"),\n    )\n    monkeypatch.setattr(agent_module, "parse_pipewire_id", lambda selected, kind: "alsa_output.test" if selected else None)\n    monkeypatch.setattr(agent_module, "play_wav_file_backend", lambda _audio, path, **kwargs: calls.update({"played": path, **kwargs}))\n\n    values = {\n        "TTS_PROVIDER": "pyttsx3",\n        "WEB_TTS_PROVIDER": "none",\n        "BACKEND_AUDIO_OUTPUT_DEVICE": "pipewire:sink:alsa_output.test",\n        "BACKEND_TTS_VOLUME": "0.8",\n        "BACKEND_AUDIO_OUTPUT_PAN": "-0.2",\n    }\n    agent_module.speak_auto_network_status(\n        "Assistant fonctionne localement",\n        Path(".env.offline"),\n        lambda _path: values,\n    )\n\n    assert calls["text"] == "Assistant fonctionne localement"\n    assert calls["output_device_index"] == 7\n    assert calls["pipewire_target"] == "alsa_output.test"\n    assert calls["volume"] == 0.8\n    assert calls["pan"] == -0.2\n    assert fake_audio.terminated is True\n\n\ndef test_offline_profiles_are_cloud_independent():\n    for relative_path in [".env.offline", "raspi_service_pack_stdio/.env.offline"]:\n        values = {}\n        for raw_line in Path(relative_path).read_text().splitlines():\n            line = raw_line.strip()\n            if not line or line.startswith("#") or "=" not in line:\n                continue\n            key, value = line.split("=", 1)\n            values[key] = value.strip().strip('"')\n\n        assert values["CONNECTIVITY_MODE"] == "offline"\n        assert values["LLM_PROVIDER"] == "ollama"\n        assert values["STT_PROVIDER"] == "local-whisper"\n        assert values["STT_INPUT"] == "backend"\n        assert values["TTS_PROVIDER"] == "pyttsx3"\n        assert values["WEB_TTS_PROVIDER"] == "none"\n        assert values["OPENAI_API_KEY_FILE"] == ""\n        assert values["ELEVENLABS_API_KEY_FILE"] == ""\n\n\ndef test_raspberry_service_has_bounded_shutdown():\n    service = Path("raspi_service_pack_stdio/livestageassistant.service").read_text()\n    assert "KillMode=control-group" in service\n    assert "TimeoutStopSec=15" in service\n''')
