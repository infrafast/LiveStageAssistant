"""Shared fully-local TTS helpers for offline runtime speech.

Piper is the preferred local backend. pyttsx3 remains available only as an
emergency migration fallback while OR3 is being Pi-validated.
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
DEFAULT_LOCAL_TTS_RATE = 145
DEFAULT_LOCAL_TTS_VOLUME = 1.0

_PIPER_CACHE: dict[str, object] = {}
_PIPER_LOCK = threading.Lock()


def _value(values: Mapping[str, object] | None, key: str, default: str = "") -> str:
    config = values or {}
    raw = config.get(key)
    if raw in (None, ""):
        raw = os.getenv(key, default)
    return str(raw or default).strip()


def local_tts_provider(values: Mapping[str, object] | None = None) -> str:
    return (_value(values, "LOCAL_TTS_PROVIDER", "piper") or "piper").lower()


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
    if local_tts_provider(values) != "piper":
        return False
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


@contextlib.contextmanager
def _quiet_native_stderr():
    saved = None
    null_fd = None
    try:
        saved = os.dup(2)
        null_fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(null_fd, 2)
        yield
    finally:
        if saved is not None:
            os.dup2(saved, 2)
            os.close(saved)
        if null_fd is not None:
            os.close(null_fd)


def _voice_search_text(voice) -> str:
    parts = [str(getattr(voice, "id", "") or ""), str(getattr(voice, "name", "") or "")]
    for language in getattr(voice, "languages", None) or []:
        if isinstance(language, bytes):
            with contextlib.suppress(Exception):
                language = language.decode("utf-8", errors="ignore")
        parts.append(str(language or ""))
    return " ".join(parts).casefold()


def _speak_pyttsx3_fallback(text: str, values: Mapping[str, object] | None = None) -> bool:
    """Emergency fallback only; not the normal OR3 offline path."""
    config = values or {}
    try:
        rate = int(float(_value(config, "LOCAL_SYSTEM_TTS_RATE", str(DEFAULT_LOCAL_TTS_RATE))))
    except ValueError:
        rate = DEFAULT_LOCAL_TTS_RATE
    try:
        volume = float(_value(config, "LOCAL_SYSTEM_TTS_VOLUME", str(DEFAULT_LOCAL_TTS_VOLUME)))
    except ValueError:
        volume = DEFAULT_LOCAL_TTS_VOLUME
    volume = max(0.0, min(1.0, volume))

    try:
        with _quiet_native_stderr():
            import pyttsx3

            tts = pyttsx3.init()
            requested = _value(config, "LOCAL_SYSTEM_TTS_VOICE")
            locale = (_value(config, "STT_LANGUAGE", "fr") or "fr").lower().replace("_", "-")
            language = locale.split("-", 1)[0]
            voices = list(tts.getProperty("voices") or [])
            selected = None
            if requested:
                requested_key = requested.casefold()
                selected = next((voice for voice in voices if requested_key in _voice_search_text(voice)), None)
            if selected is None:
                markers = {
                    "fr": ("fr-fr", "fr_", " french", "french", "français", "francais", "france"),
                    "en": ("en-us", "en-gb", " english", "english"),
                }.get(language, (language,))
                selected = next(
                    (voice for voice in voices if any(marker in _voice_search_text(voice) for marker in markers)),
                    None,
                )
            if selected is not None:
                tts.setProperty("voice", selected.id)
            tts.setProperty("rate", max(80, min(260, rate)))
            tts.setProperty("volume", volume)
            tts.say(text)
            tts.runAndWait()
            tts.stop()
        return True
    except Exception as exc:
        print(f"LSA emergency pyttsx3 fallback failed: {exc}", flush=True)
        return False


def speak_local_status(text: str, values: Mapping[str, object] | None = None) -> bool:
    """Speak a critical local status without any cloud dependency."""
    message = str(text or "").strip()
    if not message:
        return False

    provider = local_tts_provider(values)
    if provider == "piper":
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
            if _value(values, "LOCAL_TTS_PYTTSX3_FALLBACK", "true").lower() in {"1", "true", "yes", "on"}:
                print("LSA local TTS: falling back to pyttsx3 emergency path.", flush=True)
                return _speak_pyttsx3_fallback(message, values)
            return False
        finally:
            if temp_path:
                with contextlib.suppress(OSError):
                    os.unlink(temp_path)

    if provider == "pyttsx3":
        return _speak_pyttsx3_fallback(message, values)

    print(f"LSA local TTS disabled/unknown provider: {provider}", flush=True)
    return False
