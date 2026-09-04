"""Audio-device helpers for the isolated RV1 realtime runner.

RV1 must reuse the same BACKEND_AUDIO_INPUT_DEVICE/BACKEND_AUDIO_OUTPUT_DEVICE
selectors as the classic LSA backend. PipeWire selectors are opened against the
exact configured node instead of falling back to the generic PyAudio pipewire
endpoint.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
import shutil
import subprocess
import time


def parse_pipewire_selector(value: str | None, *, kind: str) -> str | None:
    selected = str(value or "").strip()
    prefix = f"pipewire:{kind}:"
    if not selected.startswith(prefix):
        return None
    target = selected[len(prefix) :].strip()
    return target or None


class PipeWireInputStream:
    """Raw PCM input stream from one exact PipeWire source."""

    def __init__(self, target: str, *, rate: int = 24000, channels: int = 1, chunk: int = 480):
        commands = [command for command in ("pw-cat", "pw-record") if shutil.which(command)]
        if not commands:
            raise RuntimeError("pw-cat or pw-record is required for configured PipeWire input")
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
        self.process: subprocess.Popen | None = None
        self.rate = rate
        self.channels = channels
        self.chunk = chunk
        self.bytes_per_frame = channels * 2
        self.target = target
        for command in commands:
            args = [command, "--record", *base_args] if Path(command).name == "pw-cat" else [command, *base_args]
            process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            time.sleep(0.05)
            if process.poll() is None:
                self.process = process
                break
            with contextlib.suppress(Exception):
                process.kill()
                process.wait(timeout=1.0)
        if self.process is None:
            raise RuntimeError(f"PipeWire input capture failed for source '{target}'")

    def read(self, chunk: int, exception_on_overflow: bool = False) -> bytes:
        del exception_on_overflow
        if self.process is None or self.process.stdout is None:
            raise RuntimeError("PipeWire input capture is not active")
        remaining = max(1, int(chunk)) * self.bytes_per_frame
        parts: list[bytes] = []
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
        process = self.process
        self.process = None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=1.0)
            if process.poll() is None:
                process.kill()
                process.wait(timeout=1.0)
        if process.stdout:
            with contextlib.suppress(Exception):
                process.stdout.close()


class PipeWireOutputStream:
    """Raw PCM output stream to one exact PipeWire sink."""

    def __init__(self, target: str, *, rate: int = 24000, channels: int = 1):
        command = shutil.which("pw-cat")
        if not command:
            raise RuntimeError("pw-cat is required for configured PipeWire realtime output")
        args = [
            command,
            "--playback",
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
        self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.05)
        if self.process.poll() is not None:
            raise RuntimeError(f"PipeWire output playback failed for sink '{target}'")
        self.rate = rate
        self.channels = channels
        self.target = target

    def write(self, pcm: bytes) -> None:
        if self.process.stdin is None or self.process.poll() is not None:
            raise RuntimeError("PipeWire output playback is not active")
        self.process.stdin.write(pcm)
        self.process.stdin.flush()

    def stop_stream(self) -> None:
        self.close()

    def close(self) -> None:
        if self.process.stdin:
            with contextlib.suppress(Exception):
                self.process.stdin.close()
        if self.process.poll() is None:
            self.process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.process.wait(timeout=1.0)
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=1.0)
