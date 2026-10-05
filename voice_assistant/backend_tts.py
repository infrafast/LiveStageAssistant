"""Backend TTS preview service for the runtime-owned WebMonitor."""

from __future__ import annotations

from pathlib import Path
import asyncio
import shutil
import subprocess
import tempfile
from typing import Any, Mapping
import wave

from .backend_audio_sample import BackendAudioSamplePlayer
from .cloud_speech import (
    DEFAULT_ELEVENLABS_VOICE_ID,
    DEFAULT_OPENAI_TTS_MODEL,
    DEFAULT_OPENAI_TTS_VOICE,
    generate_elevenlabs_tts_audio,
    generate_openai_tts_audio,
)
from .local_tts import render_piper_wav
from .realtime.engine import RealtimeEngineConfig
from .realtime.provider_factory import create_realtime_engine


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


def _pcm24k_to_wav(pcm_bytes: bytes, wav_path: Path) -> None:
    with wave.open(str(wav_path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(pcm_bytes)


def _apply_wav_gain(path: Path, gain: float) -> None:
    bounded = max(0.0, min(2.0, float(gain)))
    if abs(bounded - 1.0) < 1e-6:
        return
    with wave.open(str(path), "rb") as source:
        params = source.getparams()
        if source.getsampwidth() != 2:
            raise RuntimeError("backend TTS preview gain requires 16-bit PCM WAV")
        frames = source.readframes(source.getnframes())
    samples = bytearray(frames)
    for offset in range(0, len(samples) - 1, 2):
        sample = int.from_bytes(samples[offset : offset + 2], "little", signed=True)
        scaled = max(-32768, min(32767, int(sample * bounded)))
        samples[offset : offset + 2] = int(scaled).to_bytes(2, "little", signed=True)
    with wave.open(str(path), "wb") as target:
        target.setparams(params)
        target.writeframes(bytes(samples))


async def _render_realtime_wav(
    *,
    provider: str,
    api_key: str,
    text: str,
    model: str,
    voice: str,
    speed: float,
    wav_path: Path,
) -> None:
    engine_provider = "gemini" if provider == "gemini-live" else "openai"
    default_model = "gemini-3.1-flash-live-preview" if engine_provider == "gemini" else "gpt-realtime-2.1"
    default_voice = "Kore" if engine_provider == "gemini" else "marin"
    engine = create_realtime_engine(
        engine_provider,
        RealtimeEngineConfig(
            provider=engine_provider,
            model=str(model or default_model).strip(),
            voice=str(voice or default_voice).strip(),
            instructions=(
                "Voice preview only. Say exactly the user's text once, "
                "without adding words, greetings, markdown, or explanations."
            ),
            output_speed=max(0.6, min(1.8, float(speed or 1.0))),
            server_vad=False,
        ),
        api_key=api_key,
    )
    chunks: list[bytes] = []
    try:
        await engine.start()
        while True:
            event = await asyncio.wait_for(engine.next_event(), timeout=20.0)
            if event.type == "ready":
                break
            if event.type in {"provider_error", "connection_error", "connection_closed"}:
                raise RuntimeError(f"realtime preview failed before ready: {event.data}")
        await engine.send_text(text)
        while True:
            event = await asyncio.wait_for(engine.next_event(), timeout=30.0)
            if event.type == "audio_delta":
                audio = event.data.get("audio") or b""
                if audio:
                    chunks.append(audio)
            elif event.type == "response_done":
                break
            elif event.type in {"provider_error", "connection_error", "connection_closed"}:
                raise RuntimeError(f"realtime preview failed: {event.data}")
        if not chunks:
            raise RuntimeError("realtime preview produced no audio")
        _pcm24k_to_wav(b"".join(chunks), wav_path)
    finally:
        await engine.stop()


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
        gemini_api_key: str = "",
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        requested = options or {}
        provider = str(requested.get("provider") or _value(values, "TTS_PROVIDER", "none")).strip().lower()
        if provider in {"", "none"}:
            raise ValueError("backend TTS output is disabled")
        volume = max(0.0, min(2.0, _float(requested.get("volume"), _float(_value(values, "BACKEND_TTS_VOLUME", "1.0"), 1.0))))
        play_options = {
            "action": "play",
            "volume": 1.0,
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
            elif provider in {"openai-realtime", "gemini-live"}:
                key = gemini_api_key if provider == "gemini-live" else openai_api_key
                if not key:
                    raise ValueError(f"{provider} API key is not configured")
                asyncio.run(
                    _render_realtime_wav(
                        provider=provider,
                        api_key=key,
                        text=text,
                        model=str(requested.get("model") or ""),
                        voice=str(requested.get("voice") or ""),
                        speed=_float(requested.get("speed"), _float(_value(values, "WEB_TTS_SPEED", "1.0"), 1.0)),
                        wav_path=temp_path,
                    )
                )
            else:
                raise ValueError(f"unsupported backend TTS provider: {provider}")
            _apply_wav_gain(temp_path, volume)
            self.player.control_path(temp_path, play_options)
            return {"ok": True, "provider": provider}
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink()
                except OSError:
                    pass
