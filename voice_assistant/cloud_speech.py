"""Shared cloud speech helpers used by WebMonitor services and engines."""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any, Mapping

import openai
from elevenlabs.client import ElevenLabs
from elevenlabs.types.voice_settings import VoiceSettings


DEFAULT_OPENAI_TTS_MODEL = "gpt-4o-mini-tts"
DEFAULT_OPENAI_TTS_VOICE = "alloy"
DEFAULT_ELEVENLABS_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"
FUSED_SET_COMMAND_RE = re.compile(r"^\s*(mets|met|me)([a-zà-ÿ][a-zà-ÿ0-9_-]{3,})(\b|$)", re.IGNORECASE)


def prepare_text_for_tts(text: str) -> str:
    return str(text or "").strip()


def normalize_stt_command_text(text: str) -> str:
    cleaned = str(text or "").strip()

    def split_fused_set_command(match: re.Match[str]) -> str:
        verb = match.group(1)
        target = match.group(2)
        canonical_verb = "mets" if verb.lower() in {"me", "met", "mets"} else verb
        return f"{canonical_verb} {target}"

    return FUSED_SET_COMMAND_RE.sub(split_fused_set_command, cleaned, count=1)


def _value(values: Mapping[str, object] | None, key: str, default: str = "") -> str:
    raw = (values or {}).get(key)
    return str(raw if raw not in (None, "") else default).strip()


def _speed(value: object, default: float = 1.0) -> float:
    try:
        parsed = float(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        parsed = default
    return max(0.6, min(1.8, parsed))


def generate_openai_tts_audio(
    *,
    api_key: str,
    text: str,
    model: str = DEFAULT_OPENAI_TTS_MODEL,
    voice: str = DEFAULT_OPENAI_TTS_VOICE,
    speed: float | None = None,
) -> bytes:
    message = prepare_text_for_tts(text)
    if not message:
        raise ValueError("text is required")
    if not api_key:
        raise ValueError("OpenAI client is not configured")
    client = openai.OpenAI(api_key=api_key)
    response = client.audio.speech.create(
        model=model or DEFAULT_OPENAI_TTS_MODEL,
        voice=voice or DEFAULT_OPENAI_TTS_VOICE,
        input=message,
        response_format="mp3",
        speed=_speed(speed),
    )
    return response.read()


def generate_elevenlabs_tts_audio(
    *,
    api_key: str,
    text: str,
    voice_id: str,
    speed: float | None = None,
) -> bytes:
    message = prepare_text_for_tts(text)
    if not message:
        raise ValueError("text is required")
    if not api_key:
        raise ValueError("ElevenLabs client is not configured")
    client = ElevenLabs(api_key=api_key)
    audio = client.text_to_speech.convert(
        text=message,
        voice_id=voice_id or DEFAULT_ELEVENLABS_VOICE_ID,
        model_id="eleven_multilingual_v2",
        output_format="mp3_44100_128",
        optimize_streaming_latency="2",
        voice_settings=VoiceSettings(speed=_speed(speed)),
    )
    return audio if isinstance(audio, bytes) else b"".join(audio)


def web_text_to_speech(
    *,
    provider: str,
    text: str,
    values: Mapping[str, object],
    openai_api_key: str = "",
    elevenlabs_api_key: str = "",
    options: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    requested = options or {}
    selected = str(provider or "").strip().lower()
    speed = _speed(requested.get("speed") or _value(values, "WEB_TTS_SPEED", "1.0"))
    if selected == "openai":
        audio = generate_openai_tts_audio(
            api_key=openai_api_key,
            text=text,
            model=str(requested.get("model") or _value(values, "WEB_TTS_MODEL", DEFAULT_OPENAI_TTS_MODEL)),
            voice=str(requested.get("voice") or _value(values, "WEB_TTS_VOICE", DEFAULT_OPENAI_TTS_VOICE)),
            speed=speed,
        )
    elif selected == "elevenlabs":
        audio = generate_elevenlabs_tts_audio(
            api_key=elevenlabs_api_key,
            text=text,
            voice_id=str(requested.get("voice") or _value(values, "ELEVENLABS_VOICE_ID", DEFAULT_ELEVENLABS_VOICE_ID)),
            speed=speed,
        )
    else:
        raise ValueError("Web audio TTS is not available")
    return {
        "audio_base64": base64.b64encode(audio).decode("ascii"),
        "mime_type": "audio/mpeg",
    }


def transcribe_openai_audio(
    *,
    api_key: str,
    audio_data: bytes,
    mime_type: str,
    model: str,
    language: str = "",
    prompt: str = "",
) -> str:
    if not api_key:
        raise ValueError("OpenAI client is not configured")
    if not audio_data:
        raise ValueError("audio data is empty")
    extension = "webm"
    lowered = str(mime_type or "").lower()
    if "mp4" in lowered:
        extension = "mp4"
    elif "mpeg" in lowered or "mp3" in lowered:
        extension = "mp3"
    elif "ogg" in lowered:
        extension = "ogg"
    elif "wav" in lowered:
        extension = "wav"
    audio_buffer = BytesIO(audio_data)
    audio_buffer.name = f"web-audio.{extension}"
    kwargs: dict[str, Any] = {"model": model, "file": audio_buffer}
    if language:
        kwargs["language"] = language
    if prompt:
        kwargs["prompt"] = prompt
    response = openai.OpenAI(api_key=api_key).audio.transcriptions.create(**kwargs)
    return normalize_stt_command_text(str(response.text or "").strip())


def audio_bytes_to_wav_bytes(audio_data: bytes, mime_type: str) -> bytes:
    if "wav" in str(mime_type or "").lower():
        return audio_data
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not available")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as wav_file:
        process = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                "pipe:0",
                "-acodec",
                "pcm_s16le",
                "-ar",
                "16000",
                "-ac",
                "1",
                wav_file.name,
            ],
            input=audio_data,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
        if process.returncode != 0:
            detail = process.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"ffmpeg could not decode browser audio: {detail}")
        return Path(wav_file.name).read_bytes()
