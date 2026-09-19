from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_audio_config_widgets_and_test_controls_remain_wired():
    html = (ROOT / "assets/web/index.html").read_text(encoding="utf-8")
    js = (ROOT / "assets/web/app.js").read_text(encoding="utf-8")

    for selector_id in (
        "backend-audio-input",
        "backend-audio-test",
        "backend-audio-output",
        "backend-audio-input-gain",
        "backend-audio-output-pan",
        "startup-loader-sound",
        "startup-loader-sound-play",
    ):
        assert f'id="{selector_id}"' in html

    assert 'backendAudioTest.addEventListener("click", toggleBackendAudioTest)' in js
    assert 'fetch(apiUrl("/api/backend-audio-diagnostic")' in js
    assert 'fetch(apiUrl("/api/backend-tts-test")' in js
    assert 'output_device: backendAudioOutput.value || ""' in js
    assert 'startupLoaderSoundPlay.addEventListener("click"' in js


def test_backend_audio_selectors_are_driven_by_backend_capabilities():
    js = (ROOT / "assets/web/app.js").read_text(encoding="utf-8")
    assert 'backendAudioCapabilities.input = Array.isArray(data.backend_audio_inputs)' in js
    assert 'backendAudioCapabilities.output = Array.isArray(data.backend_audio_outputs)' in js
    assert 'backendAudioInputField.classList.toggle("hidden"' in js
    assert 'backendAudioOutputField.classList.toggle("hidden"' in js


def test_engine_gui_exposes_local_or_cloud_without_local_llm_provider():
    html = (ROOT / "assets/web/index.html").read_text(encoding="utf-8")
    js = (ROOT / "assets/web/app.js").read_text(encoding="utf-8")

    assert '<option value="local">Local déterministe</option>' in html
    assert '<option value="cloud">Cloud</option>' in html
    assert 'id="cloud-engine"' in html
    assert 'id="llm-provider"' not in html
    assert 'function selectedVoiceEngine()' in js
    assert 'return cloudEngine?.value || "classic";' in js
    assert 'const engine = selectedVoiceEngine();' in js
    assert "llmProvider" not in js
    assert "provider,\n            model," not in js
