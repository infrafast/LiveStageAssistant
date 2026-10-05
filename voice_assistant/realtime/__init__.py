"""Provider-neutral realtime voice boundary."""

from __future__ import annotations

from contextlib import contextmanager
import os


@contextmanager
def _silence_native_audio_stderr():
    """Silence ALSA/JACK probe chatter while preserving Python exceptions.

    PortAudio probes many unavailable ALSA/JACK pseudo-devices during
    initialization/open and writes directly to the process stderr file
    descriptor. Those diagnostics are not actionable when LSA subsequently
    opens the configured device successfully. We therefore suppress fd=2 only
    for the native probe call itself; raised exceptions still propagate to the
    runtime, which reports a concise LSA audio error.
    """

    saved_fd = None
    null_fd = None
    try:
        saved_fd = os.dup(2)
        null_fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(null_fd, 2)
        yield
    finally:
        if saved_fd is not None:
            os.dup2(saved_fd, 2)
            os.close(saved_fd)
        if null_fd is not None:
            os.close(null_fd)


def _install_quiet_pyaudio() -> None:
    """Wrap PyAudio constructor/open so native probe noise stays out of logs."""

    try:
        import pyaudio
    except Exception:
        return

    original = pyaudio.PyAudio
    if getattr(original, "_lsa_quiet_native_audio", False):
        return

    class QuietPyAudio(original):
        _lsa_quiet_native_audio = True

        def __init__(self, *args, **kwargs):
            with _silence_native_audio_stderr():
                super().__init__(*args, **kwargs)

        def open(self, *args, **kwargs):
            with _silence_native_audio_stderr():
                return super().open(*args, **kwargs)

    pyaudio.PyAudio = QuietPyAudio


_install_quiet_pyaudio()

from .engine import (
    RealtimeEngine,
    RealtimeEngineConfig,
    RealtimeEngineState,
    RealtimeEvent,
    RealtimeFunctionTool,
    RealtimeMCPServer,
)

__all__ = [
    "RealtimeEngine",
    "RealtimeEngineConfig",
    "RealtimeEngineState",
    "RealtimeEvent",
    "RealtimeFunctionTool",
    "RealtimeMCPServer",
]
