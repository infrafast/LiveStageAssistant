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


def test_benchmark_setup_does_not_require_system_cmake():
    content = SETUP.read_text(encoding="utf-8")
    assert 'need cmake' not in content
    assert 'build-tools-venv' in content
    assert 'pip install --upgrade cmake ninja' in content
    assert '-G Ninja' in content


def test_benchmark_exposes_live_per_sample_progress():
    content = SCRIPT.read_text(encoding="utf-8")
    assert "À décoder" in content
    assert "décodé" in content
    assert "ETA ≈" in content
    assert "Warm-up (non compté)" in content

    sherpa = (ROOT / "scripts/stt_benchmark_sherpa.py").read_text(encoding="utf-8")
    assert "À décoder" in sherpa
    assert "décodé" in sherpa
    assert "ETA ≈" in sherpa


def test_sherpa_targeted_variants_are_explicit_and_static():
    module = load_module()
    status = module.engine_status()
    assert "sherpa-onnx-zipformer-fr-int8-beam" in status
    assert "sherpa-onnx-zipformer-fr-int8-hotwords" in status

    hotwords_path = ROOT / "scripts/stt_benchmark_sherpa_hotwords.fr.txt"
    hotwords = hotwords_path.read_text(encoding="utf-8")
    for expected in ("METS", "MONTE", "BAISSE", "MUTE", "DÉMUTE", "D B", "Q L C"):
        assert expected in hotwords

    # The experiment biases only stable command structure, never live registry labels.
    for dynamic_name in (
        "GUITAR-ANTO", "GUITAR-LORAN", "GUITAR-CLODE", "BASSE-MIKE",
        "ANTO", "LAURENT", "MIKE", "CLAUDE", "BATTERIE",
    ):
        assert dynamic_name not in {line.strip() for line in hotwords.splitlines()}


def test_sherpa_setup_fetches_benchmark_only_bpe_vocab():
    content = SETUP.read_text(encoding="utf-8")
    assert "unigram_500.vocab" in content
    assert "icefall-asr-commonvoice-fr-pruned-transducer-stateless7-streaming-2023-04-02" in content


def test_targeted_engine_runs_preserve_full_baseline_aliases():
    content = SCRIPT.read_text(encoding="utf-8")
    assert "publish_latest=requested_engines is None" in content
    assert "Run ciblé: les alias benchmark_results.* du corpus complet" in content
