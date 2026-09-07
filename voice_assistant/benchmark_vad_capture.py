"""Production-like VAD capture helper for functional voice benchmarks."""

from __future__ import annotations

import asyncio
from collections import deque
import os
from pathlib import Path
import time
from typing import Any

import pyaudio

from voice_assistant.agent import DEFAULT_SILERO_VAD_MODEL, SileroVadGate, pcm_to_vad_16k_mono
from voice_assistant.realtime.audio import Pcm16MonoResampler, downmix_pcm16
from voice_assistant.realtime.service import open_configured_input

TARGET_RATE = 24000


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default)) or default))
    except (TypeError, ValueError):
        return default


async def capture_vad_utterance(
    _legacy_seconds: float,
    selected: str,
    *,
    wait_timeout: float = 15.0,
) -> tuple[bytes, dict[str, Any]]:
    """Capture one utterance using the same Silero thresholds as Classic LSA.

    The first positional argument is intentionally ignored for compatibility with
    the original fixed-window RV2E benchmark capture callback.
    """
    pa = pyaudio.PyAudio()
    stream = None
    try:
        stream, source_rate, channels, frames, name = open_configured_input(pa, selected)
        vad = SileroVadGate(
            Path(os.getenv("VAD_MODEL_PATH", str(DEFAULT_SILERO_VAD_MODEL))),
            threshold=_env_float("VAD_SPEECH_THRESHOLD", 0.5),
            neg_threshold=_env_float("VAD_NEGATIVE_THRESHOLD", 0.35),
            min_speech_ms=_env_int("VAD_MIN_SPEECH_MS", 250),
            min_silence_ms=_env_int("VAD_MIN_SILENCE_MS", 650),
            speech_pad_ms=_env_int("VAD_SPEECH_PAD_MS", 100),
            max_speech_seconds=_env_float("VAD_MAX_SPEECH_SECONDS", 8.0),
        )
        vad.reset()
        to_target = Pcm16MonoResampler(source_rate, TARGET_RATE)
        chunk_ms = frames / float(source_rate) * 1000.0
        pre_roll_count = max(1, int((vad.speech_pad_ms / max(chunk_ms, 1.0)) + 0.999))
        pre_roll: deque[bytes] = deque(maxlen=pre_roll_count)
        candidate: list[bytes] = []
        captured: list[bytes] = []
        candidate_ms = 0.0
        speech_ms = 0.0
        silence_ms = 0.0
        speech_started = False
        started_at = time.monotonic()

        print(
            f"RV2E_VAD input_selector={selected or '<default>'} resolved_input={name} "
            f"threshold={vad.threshold:.2f} neg_threshold={vad.neg_threshold:.2f} "
            f"min_speech_ms={vad.min_speech_ms} min_silence_ms={vad.min_silence_ms}",
            flush=True,
        )
        print("RV2E_VAD waiting for speech: Quel est le volume de Claude ?", flush=True)

        while True:
            if not speech_started and time.monotonic() - started_at > wait_timeout:
                raise RuntimeError("no speech detected before RV2E VAD timeout")

            raw = await asyncio.to_thread(stream.read, frames, False)
            mono = downmix_pcm16(raw, channels)
            target = to_target.process(mono)
            vad_pcm = pcm_to_vad_16k_mono(raw, source_rate=source_rate, channels=channels)
            probabilities = vad.process_pcm(vad_pcm)
            probability = max(probabilities) if probabilities else 0.0
            probability_ms = vad.chunk_ms * max(1, len(probabilities))

            if speech_started:
                if target:
                    captured.append(target)
                speech_ms += probability_ms
                if probability < vad.neg_threshold:
                    silence_ms += probability_ms
                    if silence_ms >= vad.min_silence_ms:
                        break
                else:
                    silence_ms = 0.0
                if speech_ms >= vad.max_speech_seconds * 1000.0:
                    break
                continue

            if target:
                pre_roll.append(target)
            if probability >= vad.threshold:
                if target:
                    candidate.append(target)
                candidate_ms += probability_ms
                if candidate_ms >= vad.min_speech_ms:
                    speech_started = True
                    captured = list(pre_roll) + candidate
                    speech_ms = candidate_ms
                    silence_ms = 0.0
                    print("RV2E_VAD speech detected", flush=True)
            else:
                candidate.clear()
                candidate_ms = 0.0

        pcm = b"".join(captured)
        duration = len(pcm) / 2.0 / TARGET_RATE
        if duration <= 0.0:
            raise RuntimeError("RV2E VAD captured no usable PCM")
        print(f"RV2E_VAD captured duration_s={duration:.3f} device={name}", flush=True)
        return pcm, {
            "duration_s": duration,
            "device": name,
            "selector": selected,
            "rate": TARGET_RATE,
            "capture": "silero_vad",
        }
    finally:
        if stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        pa.terminate()
