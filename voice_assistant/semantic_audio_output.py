"""Shared backend playback adapter for semantic user-feedback WAV cues."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import threading
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]


def _value(values: Mapping[str, object] | None, key: str, default: str = "") -> str:
    source = values or {}
    raw = source.get(key)
    if raw in (None, ""):
        raw = os.getenv(key, default)
    return str(raw or default).strip()


def resolve_cue_path(cue: str) -> Path:
    path = Path(str(cue or "").strip()).expanduser()
    if path.is_absolute():
        return path
    candidates = (ROOT / path, ROOT / "assets" / path)
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[-1])


def pipewire_sink(values: Mapping[str, object] | None = None) -> str:
    configured = _value(values, "BACKEND_AUDIO_OUTPUT_DEVICE")
    prefix = "pipewire:sink:"
    return configured[len(prefix) :].strip() if configured.startswith(prefix) else ""


class SemanticCuePlayer:
    """Non-blocking one-shot and loop WAV playback for semantic feedback.

    PipeWire is preferred because it safely mixes cue playback with an already
    open assistant speech stream. ALSA is retained as a fallback for systems
    without PipeWire; on exclusive ALSA devices cues may fail rather than steal
    the active speech device.
    """

    def __init__(self, values: Mapping[str, object] | None = None) -> None:
        self.values = values
        self._loop_lock = threading.Lock()
        self._loop_stop: threading.Event | None = None
        self._loop_thread: threading.Thread | None = None
        self._warned: set[str] = set()

    def _command(self, path: Path) -> list[str]:
        target = pipewire_sink(self.values)
        if shutil.which("pw-play"):
            command = ["pw-play"]
            if target:
                command.extend(["--target", target])
            command.append(str(path))
            return command
        if shutil.which("aplay"):
            return ["aplay", "-q", str(path)]
        raise RuntimeError("no pw-play/aplay available for semantic audio feedback")

    def _play_blocking(self, cue: str) -> None:
        path = resolve_cue_path(cue)
        if not path.is_file():
            raise FileNotFoundError(f"semantic cue not found: {path}")
        subprocess.run(
            self._command(path),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )

    def _play_safe(self, cue: str) -> None:
        try:
            self._play_blocking(cue)
        except Exception as exc:
            key = f"{cue}:{type(exc).__name__}"
            if key not in self._warned:
                self._warned.add(key)
                print(f"Semantic audio cue failed: cue={cue!r} error={exc}", flush=True)

    def play_once(self, cue: str) -> None:
        if not str(cue or "").strip():
            return
        threading.Thread(
            target=self._play_safe,
            args=(cue,),
            name="lsa-semantic-cue",
            daemon=True,
        ).start()

    def start_loop(self, cue: str) -> None:
        if not str(cue or "").strip():
            return
        self.stop_loop()
        stop = threading.Event()

        def worker() -> None:
            while not stop.is_set():
                self._play_safe(cue)
                if stop.wait(0.02):
                    return

        thread = threading.Thread(target=worker, name="lsa-semantic-loop", daemon=True)
        with self._loop_lock:
            self._loop_stop = stop
            self._loop_thread = thread
        thread.start()

    def stop_loop(self) -> None:
        with self._loop_lock:
            stop = self._loop_stop
            thread = self._loop_thread
            self._loop_stop = None
            self._loop_thread = None
        if stop is not None:
            stop.set()
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=0.35)

    def close(self) -> None:
        self.stop_loop()
