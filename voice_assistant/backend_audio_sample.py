"""Backend preview playback for WAV assets and speaker-profile samples."""

from __future__ import annotations

from array import array
from contextlib import suppress
from pathlib import Path
import shutil
import subprocess
import threading
import wave
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]


def _asset_wav(filename: str) -> Path:
    clean = Path(filename or "").name
    if not clean or clean != filename or Path(clean).suffix.lower() != ".wav":
        raise ValueError("audio sample must be a WAV file from assets/")
    assets_root = (ROOT / "assets").resolve()
    path = (assets_root / clean).resolve()
    if path.parent != assets_root or not path.is_file():
        raise ValueError(f"audio sample '{clean}' was not found in assets/")
    return path


def speaker_profile_sample_path(values: Mapping[str, object], profile_index: int, sample_index: int) -> Path:
    if profile_index < 1 or profile_index > 5 or sample_index < 1 or sample_index > 3:
        raise ValueError("invalid speaker profile sample")
    profile_root = Path(str(values.get("SPEAKER_PROFILES_DIR") or "data/speaker_profiles").strip()).resolve()
    path = (profile_root / f"profil{profile_index}_{sample_index}.wav").resolve()
    if path.parent != profile_root or not path.is_file():
        raise ValueError("speaker profile WAV is not available")
    return path


def _float(value: Any, default: float) -> float:
    try:
        return float(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return default


def _pipewire_target(selector: str) -> str:
    prefix = "pipewire:sink:"
    return selector[len(prefix) :].strip() if selector.startswith(prefix) else ""


def _pyaudio_output_index(selector: str) -> int | None:
    text = str(selector or "").strip()
    if not text or text.startswith("pipewire:"):
        return None
    with suppress(ValueError):
        return int(text)
    return None


def _scale_pcm_16(data: bytes, volume: float) -> bytes:
    volume = max(0.0, min(2.0, volume))
    if abs(volume - 1.0) < 1e-6:
        return data
    samples = array("h")
    samples.frombytes(data)
    if samples.itemsize != 2:
        raise RuntimeError("16-bit PCM array support is not available")
    for index, sample in enumerate(samples):
        samples[index] = max(-32768, min(32767, int(sample * volume)))
    return samples.tobytes()


class BackendAudioSamplePlayer:
    """Interruptible backend WAV preview player used by the common WebMonitor."""

    def __init__(self, values: Mapping[str, object] | None = None) -> None:
        self.values = values or {}
        self._lock = threading.Lock()
        self._stop: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[bytes] | None = None

    def update_values(self, values: Mapping[str, object] | None) -> None:
        self.values = values or {}

    def control(self, filename: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        requested = options or {}
        action = str(requested.get("action") or "play").strip().lower()
        if action == "stop":
            self.stop()
            return {"ok": True, "action": "stop"}
        if action not in {"play", "start"}:
            raise ValueError("unsupported backend audio sample action")
        path = _asset_wav(filename)
        result = self.control_path(path, requested)
        result["filename"] = path.name
        return result

    def control_path(self, path: Path, options: dict[str, Any] | None = None) -> dict[str, Any]:
        requested = options or {}
        action = str(requested.get("action") or "play").strip().lower()
        if action == "stop":
            self.stop()
            return {"ok": True, "action": "stop"}
        if action not in {"play", "start"}:
            raise ValueError("unsupported backend audio sample action")
        resolved_path = path.resolve()
        if resolved_path.suffix.lower() != ".wav" or not resolved_path.is_file():
            raise ValueError("audio sample must be an existing WAV file")
        selector = str(
            requested.get("output_device")
            or self.values.get("BACKEND_AUDIO_OUTPUT_DEVICE")
            or ""
        ).strip()
        volume = max(0.0, min(2.0, _float(requested.get("volume"), _float(self.values.get("BACKEND_TTS_VOLUME"), 1.0))))

        if action == "play":
            self._play_once(resolved_path, selector=selector, volume=volume, stop_event=None)
            return {"ok": True, "action": "play"}
        self.start(resolved_path, selector=selector, volume=volume)
        return {"ok": True, "action": "start"}

    def start(self, path: Path, *, selector: str, volume: float) -> None:
        self.stop()
        stop = threading.Event()

        def worker() -> None:
            while not stop.is_set():
                self._play_once(path, selector=selector, volume=volume, stop_event=stop)
                if stop.wait(0.02):
                    break

        thread = threading.Thread(target=worker, name="backend-audio-sample-preview", daemon=True)
        with self._lock:
            self._stop = stop
            self._thread = thread
        thread.start()

    def stop(self) -> None:
        with self._lock:
            stop = self._stop
            thread = self._thread
            process = self._process
            self._stop = None
            self._thread = None
            self._process = None
        if stop is not None:
            stop.set()
        if process is not None and process.poll() is None:
            process.terminate()
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=0.25)
            if process.poll() is None:
                process.kill()
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=0.5)

    def close(self) -> None:
        self.stop()

    def _play_once(self, path: Path, *, selector: str, volume: float, stop_event: threading.Event | None) -> None:
        target = _pipewire_target(selector)
        if target and shutil.which("pw-play"):
            self._play_pipewire(path, target=target, stop_event=stop_event)
            return
        self._play_pyaudio(path, output_device_index=_pyaudio_output_index(selector), volume=volume, stop_event=stop_event)

    def _play_pipewire(self, path: Path, *, target: str, stop_event: threading.Event | None) -> None:
        command = ["pw-play", "--target", target, str(path)]
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with self._lock:
            self._process = process
        try:
            while process.poll() is None:
                if stop_event and stop_event.wait(0.02):
                    process.terminate()
                    break
        finally:
            with self._lock:
                if self._process is process:
                    self._process = None
            if process.poll() is None:
                with suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=0.25)
                if process.poll() is None:
                    process.kill()

    def _play_pyaudio(
        self,
        path: Path,
        *,
        output_device_index: int | None,
        volume: float,
        stop_event: threading.Event | None,
    ) -> None:
        try:
            import pyaudio
        except Exception as exc:  # pragma: no cover - platform dependency
            raise RuntimeError(f"PyAudio is not available for backend sample preview: {exc}") from exc

        audio = pyaudio.PyAudio()
        stream = None
        try:
            with wave.open(str(path), "rb") as wav:
                if wav.getsampwidth() != 2:
                    raise RuntimeError("only 16-bit PCM WAV playback is supported")
                stream = audio.open(
                    format=pyaudio.paInt16,
                    channels=wav.getnchannels(),
                    rate=wav.getframerate(),
                    output=True,
                    output_device_index=output_device_index,
                    frames_per_buffer=1024,
                )
                while True:
                    if stop_event and stop_event.is_set():
                        break
                    data = wav.readframes(1024)
                    if not data:
                        break
                    stream.write(_scale_pcm_16(data, volume))
        finally:
            if stream is not None:
                with suppress(Exception):
                    stream.stop_stream()
                    stream.close()
            audio.terminate()
