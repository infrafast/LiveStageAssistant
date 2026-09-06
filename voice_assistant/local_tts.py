"""Shared Piper helpers for fully-local/offline speech.

Piper is the single local TTS implementation. There is no local-provider
selection layer and no pyttsx3 fallback in this module.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from typing import Mapping
import wave

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PIPER_VOICE = "fr_FR-siwis-medium"
DEFAULT_PIPER_DATA_DIR = ROOT / "data" / "piper"

_PIPER_CACHE: dict[str, object] = {}
_PIPER_LOCK = threading.Lock()


def _value(values: Mapping[str, object] | None, key: str, default: str = "") -> str:
    config = values or {}
    raw = config.get(key)
    if raw in (None, ""):
        raw = os.getenv(key, default)
    return str(raw or default).strip()


def piper_voice_name(values: Mapping[str, object] | None = None) -> str:
    return _value(values, "PIPER_VOICE", DEFAULT_PIPER_VOICE) or DEFAULT_PIPER_VOICE


def piper_data_dir(values: Mapping[str, object] | None = None) -> Path:
    configured = _value(values, "PIPER_DATA_DIR", str(DEFAULT_PIPER_DATA_DIR))
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    return path


def piper_model_path(values: Mapping[str, object] | None = None) -> Path:
    explicit = _value(values, "PIPER_MODEL_PATH")
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            path = (ROOT / path).resolve()
        return path
    return piper_data_dir(values) / f"{piper_voice_name(values)}.onnx"


def piper_config_path(values: Mapping[str, object] | None = None) -> Path:
    explicit = _value(values, "PIPER_CONFIG_PATH")
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            path = (ROOT / path).resolve()
        return path
    model = piper_model_path(values)
    return model.with_suffix(model.suffix + ".json")


def piper_ready(values: Mapping[str, object] | None = None) -> bool:
    model = piper_model_path(values)
    config = piper_config_path(values)
    if not model.is_file() or not config.is_file():
        return False
    try:
        import piper  # noqa: F401
    except Exception:
        return False
    return True


def _load_piper_voice(values: Mapping[str, object] | None = None):
    from piper import PiperVoice

    model = piper_model_path(values)
    config = piper_config_path(values)
    if not model.is_file():
        raise FileNotFoundError(f"Piper model not found: {model}")
    if not config.is_file():
        raise FileNotFoundError(f"Piper config not found: {config}")

    cache_key = f"{model}:{config}"
    with _PIPER_LOCK:
        voice = _PIPER_CACHE.get(cache_key)
        if voice is None:
            voice = PiperVoice.load(str(model), config_path=str(config))
            _PIPER_CACHE.clear()
            _PIPER_CACHE[cache_key] = voice
        return voice


def render_piper_wav(
    text: str,
    output_path: str | Path,
    values: Mapping[str, object] | None = None,
) -> Path:
    """Render text to a WAV file using the configured Piper voice."""
    message = str(text or "").strip()
    if not message:
        raise ValueError("Piper text is empty")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    voice = _load_piper_voice(values)

    try:
        length_scale = float(_value(values, "PIPER_LENGTH_SCALE", "1.0") or "1.0")
    except ValueError:
        length_scale = 1.0
    try:
        volume = float(_value(values, "BACKEND_TTS_VOLUME", "1.0") or "1.0")
    except ValueError:
        volume = 1.0
    volume = max(0.0, min(2.0, volume))

    try:
        from piper import SynthesisConfig

        synth_config = SynthesisConfig(volume=volume, length_scale=max(0.5, min(2.0, length_scale)))
    except Exception:
        synth_config = None

    with wave.open(str(output), "wb") as wav_file:
        if synth_config is None:
            voice.synthesize_wav(message, wav_file)
        else:
            voice.synthesize_wav(message, wav_file, syn_config=synth_config)
    return output


def _pipewire_target(values: Mapping[str, object] | None = None) -> str:
    configured = _value(values, "BACKEND_AUDIO_OUTPUT_DEVICE")
    prefix = "pipewire:sink:"
    if configured.startswith(prefix):
        return configured[len(prefix) :].strip()
    return ""


def play_local_wav(path: str | Path, values: Mapping[str, object] | None = None) -> None:
    """Play a local WAV through PipeWire when available, else ALSA default."""
    audio_path = str(Path(path))
    target = _pipewire_target(values)
    if shutil.which("pw-play"):
        command = ["pw-play"]
        if target:
            command.extend(["--target", target])
        command.append(audio_path)
        subprocess.run(command, check=True)
        return
    if shutil.which("aplay"):
        subprocess.run(["aplay", "-q", audio_path], check=True)
        return
    raise RuntimeError("Neither pw-play nor aplay is available for local TTS playback")


def speak_local_status(text: str, values: Mapping[str, object] | None = None) -> bool:
    """Speak a critical local status through Piper without cloud dependency."""
    message = str(text or "").strip()
    if not message:
        return False

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
            temp_path = temp_file.name
        render_piper_wav(message, temp_path, values)
        print(
            f"LSA local TTS: provider=piper voice={piper_voice_name(values)} model={piper_model_path(values)}",
            flush=True,
        )
        play_local_wav(temp_path, values)
        return True
    except Exception as exc:
        print(f"LSA Piper local TTS failed: {exc}", flush=True)
        return False
    finally:
        if temp_path:
            with contextlib.suppress(OSError):
                os.unlink(temp_path)
