from pathlib import Path
from unittest import mock

from voice_assistant import agent
from voice_assistant import backend_audio_sample
from voice_assistant import local_tts


def test_local_tts_routes_numeric_output_through_backend_player(tmp_path):
    wav = tmp_path / "status.wav"
    wav.write_bytes(b"RIFF")

    player = mock.Mock()
    with mock.patch.object(backend_audio_sample, "BackendAudioSamplePlayer", return_value=player):
        local_tts.play_local_wav(wav, {"BACKEND_AUDIO_OUTPUT_DEVICE": "7"})

    backend_audio_sample.BackendAudioSamplePlayer.assert_not_called if False else None
    player.control_path.assert_called_once_with(Path(wav), {"action": "play", "volume": 1.0})


def test_configured_pipewire_output_never_falls_back_to_default(tmp_path):
    wav = tmp_path / "status.wav"
    wav.write_bytes(b"RIFF")
    player = backend_audio_sample.BackendAudioSamplePlayer(
        {"BACKEND_AUDIO_OUTPUT_DEVICE": "pipewire:sink:alsa_output.test"}
    )

    with mock.patch.object(backend_audio_sample.shutil, "which", return_value=None):
        try:
            player.control_path(wav, {"action": "play"})
        except RuntimeError as error:
            assert "pw-play is unavailable" in str(error)
        else:
            raise AssertionError("configured PipeWire output must not silently fall back")


def test_backend_input_stream_uses_configured_pyaudio_index():
    audio = mock.Mock()
    expected = object()
    audio.open.return_value = expected

    stream = agent.open_backend_input_stream(
        audio,
        audio_format=agent.pyaudio.paInt16,
        channels=1,
        rate=16000,
        chunk=1024,
        input_device_index=5,
        pipewire_target=None,
    )

    assert stream is expected
    assert audio.open.call_args.kwargs["input_device_index"] == 5
    assert audio.open.call_args.kwargs["input"] is True


def test_backend_input_stream_uses_configured_pipewire_source():
    expected = object()
    with mock.patch.object(agent, "PipeWireInputStream", return_value=expected) as factory:
        stream = agent.open_backend_input_stream(
            mock.Mock(),
            audio_format=agent.pyaudio.paInt16,
            channels=1,
            rate=16000,
            chunk=1024,
            input_device_index=None,
            pipewire_target="alsa_input.test",
        )

    assert stream is expected
    factory.assert_called_once_with("alsa_input.test", rate=16000, channels=1, chunk=1024)
