"""Production-like VAD capture helper for functional voice benchmarks."""

from __future__ import annotations

import asyncio
from collections import deque
import math
import os
from pathlib import Path
import time
from typing import Any

import numpy as np
import pyaudio

from voice_assistant.agent import DEFAULT_SILERO_VAD_MODEL, SileroVadGate, pcm_to_vad_16k_mono
from voice_assistant.realtime.audio import Pcm16MonoResampler, downmix_pcm16
from voice_assistant.realtime.service import open_configured_input

TARGET_RATE = 24000
BENCHMARK_PROMPT = "Quel est le volume de clic ?"


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


def _apply_gain(pcm: bytes, gain: float) -> bytes:
    if not pcm or abs(gain - 1.0) < 1e-6:
        return pcm
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    samples *= float(gain)
    np.clip(samples, -32768.0, 32767.0, out=samples)
    return samples.astype(np.int16).tobytes()


def _peak_dbfs(pcm: bytes) -> float:
    if not pcm:
        return -120.0
    samples = np.frombuffer(pcm, dtype=np.int16)
    if samples.size == 0:
        return -120.0
    peak = float(np.max(np.abs(samples.astype(np.int32)))) / 32768.0
    if peak <= 1e-9:
        return -120.0
    return 20.0 * math.log10(peak)


async def capture_vad_utterance(
    _legacy_seconds: float,
    selected: str,
    *,
    wait_timeout: float = 15.0,
) -> tuple[bytes, dict[str, Any]]:
    """Capture one utterance with Silero VAD using LSA profile thresholds."""
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
        input_gain = max(0.5, min(2.0, _env_float("BACKEND_AUDIO_INPUT_GAIN", 1.0)))
        to_target = Pcm16MonoResampler(source_rate, TARGET_RATE)
        chunk_ms = frames / float(source_rate) * 1000.0
        pre_roll_ms = max(float(vad.speech_pad_ms), 600.0)
        pre_roll_count = max(1, int((pre_roll_ms / max(chunk_ms, 1.0)) + 0.999))
        pre_roll: deque[bytes] = deque(maxlen=pre_roll_count)
        captured: list[bytes] = []
        speech_ms = 0.0
        silence_ms = 0.0
        speech_started = False
        started_at = time.monotonic()
        next_diag = started_at + 1.0
        max_vad_seen = 0.0
        max_peak_dbfs = -120.0
        evidence: deque[tuple[float, float]] = deque()
        evidence_window_s = 0.7
        strong_hits_required = 2

        print(
            f"RV2E_VAD input_selector={selected or '<default>'} resolved_input={name} "
            f"threshold={vad.threshold:.2f} neg_threshold={vad.neg_threshold:.2f} "
            f"min_speech_ms={vad.min_speech_ms} min_silence_ms={vad.min_silence_ms} "
            f"input_gain={input_gain:.2f}",
            flush=True,
        )
        print(f"RV2E_VAD waiting for speech: {BENCHMARK_PROMPT}", flush=True)

        while True:
            if not speech_started and time.monotonic() - started_at > wait_timeout:
                raise RuntimeError(
                    "no speech detected before RV2E VAD timeout "
                    f"(max_vad={max_vad_seen:.3f}, peak_dbfs={max_peak_dbfs:.1f})"
                )

            raw = await asyncio.to_thread(stream.read, frames, False)
            raw = _apply_gain(raw, input_gain)
            mono = downmix_pcm16(raw, channels)
            target = to_target.process(mono)
            vad_pcm = pcm_to_vad_16k_mono(raw, source_rate=source_rate, channels=channels)
            probabilities = vad.process_pcm(vad_pcm)
            probability = max(probabilities) if probabilities else 0.0
            probability_ms = vad.chunk_ms * max(1, len(probabilities))
            peak_dbfs = _peak_dbfs(mono)
            max_vad_seen = max(max_vad_seen, probability)
            max_peak_dbfs = max(max_peak_dbfs, peak_dbfs)

            now = time.monotonic()
            if not speech_started and now >= next_diag:
                positive_ms = sum(ms for _, ms in evidence)
                print(
                    f"RV2E_VAD_DIAG peak_dbfs={peak_dbfs:.1f} max_peak_dbfs={max_peak_dbfs:.1f} "
                    f"vad={probability:.3f} max_vad={max_vad_seen:.3f} evidence_ms={positive_ms:.0f} hits={len(evidence)}",
                    flush=True,
                )
                next_diag = now + 1.0

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

            while evidence and now - evidence[0][0] > evidence_window_s:
                evidence.popleft()
            if probability >= vad.threshold:
                evidence.append((now, probability_ms))

            positive_ms = sum(ms for _, ms in evidence)
            strong_hits = len(evidence)
            if strong_hits >= strong_hits_required and (
                positive_ms >= min(float(vad.min_speech_ms), 96.0) or max_vad_seen >= 0.85
            ):
                speech_started = True
                captured = list(pre_roll)
                speech_ms = max(positive_ms, probability_ms)
                silence_ms = 0.0
                print(
                    f"RV2E_VAD speech detected vad={probability:.3f} max_vad={max_vad_seen:.3f} "
                    f"peak_dbfs={peak_dbfs:.1f} evidence_ms={positive_ms:.0f} hits={strong_hits}",
                    flush=True,
                )

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
            "input_gain": input_gain,
            "max_vad": max_vad_seen,
            "max_peak_dbfs": max_peak_dbfs,
        }
    finally:
        if stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        pa.terminate()