"""Backend TTS preview service for the runtime-owned WebMonitor."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Mapping

from .backend_audio_sample import BackendAudioSamplePlayer
from .cloud_speech import (
    DEFAULT_ELEVENLABS_VOICE_ID,
    DEFAULT_OPENAI_TTS_MODEL,
    DEFAULT_OPENAI_TTS_VOICE,
    generate_elevenlabs_tts_audio,
    generate_openai_tts_audio,
)
from .local_tts import render_piper_wav


def _value(values: Mapping[str, object], key: str, default: str = "") -> str:
    raw = values.get(key)
    return str(raw if raw not in (None, "") else default).strip()


def _float(value: object, default: float) -> float:
    try:
        parsed = float(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        parsed = default
    return parsed


def _mp3_to_wav(mp3_bytes: bytes, wav_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required for backend-controlled MP3 playback")
    process = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "mp3",
            "-i",
            "pipe:0",
            "-acodec",
            "pcm_s16le",
            str(wav_path),
        ],
        input=mp3_bytes,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode != 0:
        detail = process.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg could not decode generated speech: {detail}")


class BackendTtsTester:
    """Render a short phrase and play it through the configured backend output."""

    def __init__(self, player: BackendAudioSamplePlayer | None = None) -> None:
        self.player = player or BackendAudioSamplePlayer()

    def test(
        self,
        text: str,
        *,
        values: Mapping[str, object],
        openai_api_key: str = "",
        elevenlabs_api_key: str = "",
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        requested = options or {}
        provider = str(requested.get("provider") or _value(values, "TTS_PROVIDER", "none")).strip().lower()
        if provider in {"", "none"}:
            raise ValueError("backend TTS output is disabled")
        volume = max(0.0, min(2.0, _float(requested.get("volume"), _float(_value(values, "BACKEND_TTS_VOLUME", "1.0"), 1.0))))
        play_options = {
            "action": "play",
            "volume": volume,
            "pan": max(-1.0, min(1.0, _float(requested.get("pan"), _float(_value(values, "BACKEND_AUDIO_OUTPUT_PAN", "0"), 0.0)))),
            "output_device": str(requested.get("output_device") or _value(values, "BACKEND_AUDIO_OUTPUT_DEVICE")),
        }
        self.player.update_values(values)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
                temp_path = Path(handle.name)
            if provider == "piper":
                render_piper_wav(text, temp_path, values)
            elif provider == "openai":
                audio = generate_openai_tts_audio(
                    api_key=openai_api_key,
                    text=text,
                    model=str(requested.get("model") or _value(values, "WEB_TTS_MODEL", DEFAULT_OPENAI_TTS_MODEL)),
                    voice=str(requested.get("voice") or _value(values, "WEB_TTS_VOICE", DEFAULT_OPENAI_TTS_VOICE)),
                    speed=_float(requested.get("speed"), _float(_value(values, "WEB_TTS_SPEED", "1.0"), 1.0)),
                )
                _mp3_to_wav(audio, temp_path)
            elif provider == "elevenlabs":
                audio = generate_elevenlabs_tts_audio(
                    api_key=elevenlabs_api_key,
                    text=text,
                    voice_id=str(requested.get("voice") or _value(values, "ELEVENLABS_VOICE_ID", DEFAULT_ELEVENLABS_VOICE_ID)),
                    speed=_float(requested.get("speed"), _float(_value(values, "WEB_TTS_SPEED", "1.0"), 1.0)),
                )
                _mp3_to_wav(audio, temp_path)
            else:
                raise ValueError(f"unsupported backend TTS provider: {provider}")
            self.player.control_path(temp_path, play_options)
            return {"ok": True, "provider": provider}
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink()
                except OSError:
                    pass
