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
