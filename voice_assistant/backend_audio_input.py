"""Shared backend microphone capture and diagnostic services."""

from __future__ import annotations

import base64
from contextlib import suppress
import io
import math
from pathlib import Path
import shutil
import subprocess
import threading
import time
from typing import Any, Mapping
import wave

import numpy as np
import pyaudio


DIAGNOSTIC_SECONDS = 7.0
CAPTURE_SECONDS = 10.0
MIN_DIAGNOSTIC_SECONDS = 3.0
MAX_DIAGNOSTIC_SECONDS = 12.0
MIN_CAPTURE_SECONDS = 3.0
MAX_CAPTURE_SECONDS = 10.0
PIPEWIRE_CAPTURE_RATE = 16000
PIPEWIRE_CAPTURE_CHANNELS = 1
PIPEWIRE_CAPTURE_CHUNK = 1024
MIN_GOOD_SPEECH_DBFS = -40.0
MIN_USABLE_SPEECH_DBFS = -56.0


def _float(value: object, default: float) -> float:
    try:
        parsed = float(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        parsed = default
    return parsed


def _int_selector(value: str | None) -> int | None:
    text = str(value or "").strip()
    if not text or text.startswith("pipewire:"):
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _pipewire_target(value: str | None) -> str:
    prefix = "pipewire:source:"
    text = str(value or "").strip()
    return text[len(prefix) :].strip() if text.startswith(prefix) else ""


def _input_device_detail(audio: pyaudio.PyAudio, index: int | None) -> str:
    if index is None:
        info = audio.get_default_input_device_info()
    else:
        info = audio.get_device_info_by_index(index)
    return f"{int(info.get('index', index or 0))}: {info.get('name') or 'default input'}"


def _open_input(audio: pyaudio.PyAudio, index: int | None):
    rate = 16000
    channels = 1
    chunk = 1024
    stream = audio.open(
        format=pyaudio.paInt16,
        channels=channels,
        rate=rate,
        input=True,
        input_device_index=index,
        frames_per_buffer=chunk,
    )
    return stream, channels, rate, chunk


class PipeWireInputStream:
    def __init__(self, target: str, *, rate: int, channels: int, chunk: int) -> None:
        commands = [command for command in ("pw-cat", "pw-record") if shutil.which(command)]
        if not commands:
            raise RuntimeError("pw-cat or pw-record is required for PipeWire backend input")
        base_args = [
            "--raw",
            "--target",
            target,
            "--format",
            "s16",
            "--rate",
            str(rate),
            "--channels",
            str(channels),
            "-",
        ]
        self.process: subprocess.Popen[bytes] | None = None
        self.bytes_per_frame = channels * 2
        for command in commands:
            args = [command, "--record", *base_args] if Path(command).name == "pw-cat" else [command, *base_args]
            process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            time.sleep(0.05)
            if process.poll() is None:
                self.process = process
                break
            with suppress(Exception):
                process.kill()
                process.wait(timeout=1.0)
            if process.stdout:
                with suppress(Exception):
                    process.stdout.close()
        if self.process is None:
            raise RuntimeError(f"PipeWire input capture failed for source '{target}'")

    def read(self, chunk: int, exception_on_overflow: bool = False) -> bytes:
        del exception_on_overflow
        if self.process is None or not self.process.stdout:
            raise RuntimeError("PipeWire input capture has no stdout stream")
        expected = max(1, int(chunk)) * self.bytes_per_frame
        parts: list[bytes] = []
        remaining = expected
        while remaining > 0:
            data = self.process.stdout.read(remaining)
            if not data:
                raise RuntimeError("PipeWire input capture stopped")
            parts.append(data)
            remaining -= len(data)
        return b"".join(parts)

    def stop_stream(self) -> None:
        self.close()

    def close(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=1.0)
        if self.process.stdout:
            with suppress(Exception):
                self.process.stdout.close()


def _wav_bytes(pcm: bytes, *, channels: int, rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(rate)
        wav_file.writeframes(pcm)
    return buffer.getvalue()


def _dbfs(value: float) -> float:
    return 20.0 * math.log10(max(value, 1e-9))


def _apply_gain(data: bytes, gain: float) -> bytes:
    if abs(gain - 1.0) < 1e-6:
        return data
    samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
    samples = np.clip(samples * gain, -32768, 32767).astype(np.int16)
    return samples.tobytes()


class BackendAudioInputService:
    """Owns backend microphone diagnostic/capture locks for the common runtime."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop_capture = threading.Event()

    def stop_capture(self) -> None:
        self._stop_capture.set()

    def _capture_pcm(
        self,
        *,
        values: Mapping[str, object],
        selected_device: str,
        duration_seconds: float,
        input_gain: float,
        stop_event: threading.Event | None = None,
    ) -> tuple[bytes, str, int, int]:
        audio = pyaudio.PyAudio()
        stream = None
        try:
            selector = selected_device or str(values.get("BACKEND_AUDIO_INPUT_DEVICE") or "")
            target = _pipewire_target(selector)
            if target:
                detail = f"PipeWire source: {target}"
                channels = PIPEWIRE_CAPTURE_CHANNELS
                rate = PIPEWIRE_CAPTURE_RATE
                chunk = PIPEWIRE_CAPTURE_CHUNK
                stream = PipeWireInputStream(target, rate=rate, channels=channels, chunk=chunk)
            else:
                index = _int_selector(selector)
                detail = _input_device_detail(audio, index)
                stream, channels, rate, chunk = _open_input(audio, index)
            gain = max(0.5, min(2.0, input_gain))
            frame_count = max(1, math.ceil(duration_seconds * rate / chunk))
            frames: list[bytes] = []
            for _ in range(frame_count):
                if stop_event is not None and stop_event.is_set():
                    break
                data = stream.read(chunk, exception_on_overflow=False)
                frames.append(_apply_gain(data, gain))
            return b"".join(frames), detail, channels, rate
        finally:
            if stream is not None:
                with suppress(Exception):
                    stream.stop_stream()
                    stream.close()
            audio.terminate()

    def capture_speaker_sample(
        self,
        *,
        values: Mapping[str, object],
        selected_device: str = "",
        duration_seconds: float = CAPTURE_SECONDS,
    ) -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("the backend microphone is busy")
        self._stop_capture.clear()
        try:
            duration = max(MIN_CAPTURE_SECONDS, min(MAX_CAPTURE_SECONDS, float(duration_seconds)))
            gain = max(0.5, min(2.0, _float(values.get("BACKEND_AUDIO_INPUT_GAIN"), 1.0)))
            pcm, detail, channels, rate = self._capture_pcm(
                values=values,
                selected_device=selected_device,
                duration_seconds=duration,
                input_gain=gain,
                stop_event=self._stop_capture,
            )
            if not pcm:
                raise ValueError("no backend microphone audio was captured")
            wav = _wav_bytes(pcm, channels=channels, rate=rate)
            return {
                "ok": True,
                "device": detail,
                "input_gain": round(gain, 2),
                "format": f"{channels}ch/{rate}Hz",
                "duration_seconds": round(len(pcm) / (rate * channels * 2), 1),
                "audio_base64": base64.b64encode(wav).decode("ascii"),
            }
        finally:
            self._stop_capture.clear()
            self._lock.release()

    def diagnose(
        self,
        *,
        values: Mapping[str, object],
        selected_device: str = "",
        input_gain: float = 1.0,
        duration_seconds: float = DIAGNOSTIC_SECONDS,
    ) -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("the backend microphone is busy")
        try:
            duration = max(MIN_DIAGNOSTIC_SECONDS, min(MAX_DIAGNOSTIC_SECONDS, float(duration_seconds)))
            gain = max(0.5, min(2.0, input_gain))
            pcm, detail, channels, rate = self._capture_pcm(
                values=values,
                selected_device=selected_device,
                duration_seconds=duration,
                input_gain=gain,
            )
            if not pcm:
                raise RuntimeError("the backend microphone returned no audio data")
            samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            if samples.size == 0:
                raise RuntimeError("the backend microphone returned no audio samples")
            rms = float(np.sqrt(np.mean(np.square(samples))))
            peak = float(np.max(np.abs(samples)))
            clipping_ratio = float(np.count_nonzero(np.abs(samples) >= 0.998) / samples.size)
            speech_dbfs = round(_dbfs(rms), 1)
            peak_dbfs = round(_dbfs(peak), 1)
            issues: list[str] = []
            verdict = "green"
            if rms < 0.001 or peak < 0.005:
                issues.append("no_signal")
                verdict = "red"
            else:
                if speech_dbfs < MIN_USABLE_SPEECH_DBFS:
                    issues.append("very_low")
                    verdict = "red"
                elif speech_dbfs < MIN_GOOD_SPEECH_DBFS:
                    issues.append("low")
                    verdict = "orange"
                if speech_dbfs > -9.0:
                    issues.append("high")
                    verdict = "orange"
                if clipping_ratio >= 0.01:
                    issues.append("severe_clipping")
                    verdict = "red"
                elif clipping_ratio >= 0.0005 or peak >= 0.995:
                    issues.append("clipping")
                    if verdict == "green":
                        verdict = "orange"
            if not issues:
                issues.append("good")
            wav = _wav_bytes(pcm, channels=channels, rate=rate)
            return {
                "ok": True,
                "verdict": verdict,
                "issues": issues,
                "reference_vad": {"threshold": 0.0, "min_speech_ms": 0},
                "configured_vad": {
                    "evaluable": rms >= 0.001 and peak >= 0.005,
                    "accepted": rms >= 0.001 and peak >= 0.005,
                    "threshold": round(_float(values.get("VAD_SPEECH_THRESHOLD"), 0.5), 2),
                    "min_speech_ms": int(_float(values.get("VAD_MIN_SPEECH_MS"), 250)),
                    "speech_duration_seconds": round(duration, 1),
                    "continuous_speech_duration_seconds": round(duration, 1),
                },
                "device": detail,
                "input_gain": round(gain, 2),
                "format": f"{channels}ch/{rate}Hz",
                "duration_seconds": round(len(pcm) / (rate * channels * 2), 1),
                "metrics": {
                    "speech_duration_seconds": round(duration, 1),
                    "continuous_speech_duration_seconds": round(duration, 1),
                    "speech_rms_dbfs": speech_dbfs,
                    "noise_rms_dbfs": None,
                    "snr_db": None,
                    "peak_dbfs": peak_dbfs,
                    "clipping_percent": round(clipping_ratio * 100.0, 2),
                    "vad_probability": None,
                },
                "audio_data_url": f"data:audio/wav;base64,{base64.b64encode(wav).decode('ascii')}",
            }
        finally:
            self._lock.release()
