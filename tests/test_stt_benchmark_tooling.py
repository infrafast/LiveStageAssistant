from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/stt_benchmark.py"
SETUP = ROOT / "scripts/stt_benchmark_setup.sh"


def load_module():
    spec = importlib.util.spec_from_file_location("stt_benchmark_tool", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_benchmark_corpus_keeps_historical_phrases_and_three_take_default():
    module = load_module()
    assert module.DEFAULT_TAKES == 3
    assert len(module.PHRASES) == 16
    assert module.PHRASES[0]["text"] == "mets guitar-clode à moins cinq dB"
    assert module.PHRASES[11]["text"] == "monte le son de la batterie de deux dB"
    assert module.PHRASES[12]["text"] == "mets anto à moins cinq dB"
    assert module.PHRASES[14]["domain"] == "qlc"


def test_benchmark_audio_endpoint_parser_supports_current_pipewire_profile_shape():
    module = load_module()
    assert module.endpoint(
        "pipewire:source:alsa_input.usb-Test.analog-stereo",
        "source",
    ) == ("pipewire", "alsa_input.usb-Test.analog-stereo")
    assert module.endpoint(
        "pipewire:sink:alsa_output.usb-Test.analog-stereo",
        "sink",
    ) == ("pipewire", "alsa_output.usb-Test.analog-stereo")
    assert module.endpoint("7", "source") == ("pyaudio", 7)
    assert module.endpoint("", "source") == ("default", None)


def test_benchmark_setup_shell_has_valid_syntax():
    bash = shutil.which("bash")
    if bash is None:
        return
    subprocess.run([bash, "-n", str(SETUP)], check=True)
