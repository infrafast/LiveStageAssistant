"""Shared LiveStageAssistant startup feedback lifecycle.

The runtime launcher owns startup feedback independently from the selected voice
engine. It starts the configured loader audio as early as practical, keeps it
looping while the engine initializes, and stops it immediately before the
engine's own ready/connectivity announcement begins.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
from typing import Mapping


class StartupLoader:
    """Loop one configured startup WAV until the common runtime declares READY."""

    def __init__(self, root: Path, values: Mapping[str, object]) -> None:
        self.root = Path(root)
        self.values = values
        self.enabled = self._bool_value("STARTUP_LOADER_SOUND_ENABLED", False)
        self.path = self._resolve_asset(str(values.get("STARTUP_LOADER_SOUND_FILE") or "loader.wav"))
        self.output_selector = str(values.get("BACKEND_AUDIO_OUTPUT_DEVICE") or "").strip()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def _bool_value(self, key: str, default: bool) -> bool:
        raw = self.values.get(key)
        if raw is None:
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    def _resolve_asset(self, value: str) -> Path:
        path = Path(os.path.expandvars(value)).expanduser()
        if path.is_absolute():
            return path
        candidates = [self.root / path, self.root / "assets" / path]
        return next((candidate for candidate in candidates if candidate.is_file()), candidates[-1])

    def _pipewire_target(self) -> str | None:
        prefix = "pipewire:sink:"
        if not self.output_selector.startswith(prefix):
            return None
        target = self.output_selector[len(prefix):].strip()
        return target or None

    def start(self) -> None:
        if not self.enabled:
            return
        if not self.path.is_file():
            print(f"Startup loader unavailable: {self.path}", flush=True)
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="lsa-startup-loader", daemon=True)
        self._thread.start()
        print(f"Startup loader ON: {self.path}", flush=True)

    def _command(self) -> list[str] | None:
        if shutil.which("pw-play"):
            command = ["pw-play"]
            target = self._pipewire_target()
            if target:
                command += ["--target", target]
            command.append(str(self.path))
            return command
        if shutil.which("aplay"):
            return ["aplay", "-q", str(self.path)]
        return None

    def _run(self) -> None:
        command = self._command()
        if command is None:
            print("Startup loader unavailable: pw-play/aplay not found", flush=True)
            return
        while not self._stop.is_set():
            try:
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                )
                with self._lock:
                    self._process = process
                while process.poll() is None and not self._stop.wait(0.05):
                    pass
                if self._stop.is_set() and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=0.5)
            except Exception as exc:
                print(f"Startup loader failed: {exc}", flush=True)
                return
            finally:
                with self._lock:
                    self._process = None
            if not self._stop.is_set():
                time.sleep(0.03)

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass
        thread = self._thread
        self._thread = None
        if thread and thread.is_alive():
            thread.join(timeout=1.0)
        print("Startup loader OFF", flush=True)


def classic_ready_marker(root: Path, values: Mapping[str, object]) -> str:
    """Return the localized Classic ready sentence printed before its TTS call."""
    locale = str(values.get("STT_LANGUAGE") or "fr").strip().lower().split("-", 1)[0] or "fr"
    path = Path(root) / "assets" / "i18n" / f"{locale}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        marker = str((data.get("startup") or {}).get("ready") or "").strip()
        if marker:
            return marker
    except Exception:
        pass
    return "Assistant vocal prêt à exécuter des commandes."
