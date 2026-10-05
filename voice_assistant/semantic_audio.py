"""Engine-neutral semantic audio feedback contract.

This module intentionally knows nothing about Classic, OpenAI Realtime, MCP,
Piper or any other provider. Engines/runtime publish semantic states; an audio
adapter decides which configured cue to play.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from typing import Callable, Mapping


class SemanticAudioState(str, Enum):
    STARTING = "starting"
    READY = "ready"
    WAIT_WAKE = "wait_wake"
    WAKE_DETECTED = "wake_detected"
    LISTENING = "listening"
    PROCESSING = "processing"
    RESULT_READY = "result_ready"
    SPEAKING = "speaking"
    IDLE = "idle"


@dataclass(frozen=True)
class SemanticAudioConfig:
    startup: str = ""
    ready: str = ""
    listening: str = ""
    wake_detected: str = ""
    thinking: str = ""
    result_ready: str = ""

    @classmethod
    def from_env(cls, values: Mapping[str, str] | None = None) -> "SemanticAudioConfig":
        env = values if values is not None else os.environ
        startup_enabled = str(env.get("STARTUP_LOADER_SOUND_ENABLED", "false") or "false").strip().lower() in {
            "1", "true", "yes", "on"
        }
        return cls(
            startup=str(env.get("STARTUP_LOADER_SOUND_FILE", "") or "").strip() if startup_enabled else "",
            ready=str(env.get("READY_SOUND_FILE", "") or "").strip(),
            listening=str(env.get("LISTENING_SOUND_FILE", "") or "").strip(),
            wake_detected=str(env.get("WAKE_DETECTED_SOUND_FILE", "") or "").strip(),
            thinking=str(env.get("THINKING_SOUND_FILE", "") or "").strip(),
            result_ready=str(env.get("COMMAND_ACK_SOUND_FILE", "") or "").strip(),
        )

    def cue_for(self, state: SemanticAudioState) -> str:
        return {
            SemanticAudioState.STARTING: self.startup,
            SemanticAudioState.READY: self.ready,
            SemanticAudioState.WAIT_WAKE: self.listening,
            SemanticAudioState.LISTENING: self.listening,
            SemanticAudioState.WAKE_DETECTED: self.wake_detected,
            SemanticAudioState.PROCESSING: self.thinking,
            SemanticAudioState.RESULT_READY: self.result_ready,
        }.get(state, "")


class SemanticAudioController:
    """State machine that maps semantic states to cue-player callbacks.

    The controller is deliberately audio-backend neutral. The caller provides:
    - ``play_once(cue)`` for one-shot cues;
    - ``start_loop(cue)`` for loader/thinking loops;
    - ``stop_loop()`` for the currently active loop.

    Repeating the same state is a no-op. Leaving STARTING or PROCESSING always
    stops the active loop before any one-shot cue is emitted.
    """

    def __init__(
        self,
        config: SemanticAudioConfig,
        *,
        play_once: Callable[[str], None],
        start_loop: Callable[[str], None],
        stop_loop: Callable[[], None],
        on_state: Callable[[SemanticAudioState], None] | None = None,
    ) -> None:
        self.config = config
        self._play_once = play_once
        self._start_loop = start_loop
        self._stop_loop = stop_loop
        self._on_state = on_state
        self.state: SemanticAudioState | None = None
        self._loop_state: SemanticAudioState | None = None

    def transition(self, state: SemanticAudioState) -> bool:
        if state == self.state:
            return False

        if self._loop_state is not None and state != self._loop_state:
            self._stop_loop()
            self._loop_state = None

        self.state = state
        if self._on_state is not None:
            self._on_state(state)

        cue = self.config.cue_for(state)
        if not cue:
            return True

        if state in {SemanticAudioState.STARTING, SemanticAudioState.PROCESSING}:
            self._start_loop(cue)
            self._loop_state = state
        elif state in {
            SemanticAudioState.READY,
            SemanticAudioState.WAIT_WAKE,
            SemanticAudioState.WAKE_DETECTED,
            SemanticAudioState.LISTENING,
            SemanticAudioState.RESULT_READY,
        }:
            self._play_once(cue)
        return True

    def close(self) -> None:
        if self._loop_state is not None:
            self._stop_loop()
            self._loop_state = None


@dataclass(frozen=True)
class VoiceOutputGains:
    """Independent speech-output gains by execution locality, not by engine.

    CLOUD_TTS_OUTPUT_GAIN applies to cloud-produced speech, including realtime
    speech streams. LOCAL_TTS_OUTPUT_GAIN applies to fully local speech.

    BACKEND_TTS_VOLUME remains a compatibility fallback for both values until
    deployed profiles have explicit Cloud/Local gains. Once either new key is
    present it is independent from the other.
    """

    cloud: float = 1.0
    local: float = 1.0

    @staticmethod
    def _bounded(value: object, default: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = default
        return max(0.0, min(2.0, parsed))

    @classmethod
    def from_env(cls, values: Mapping[str, str] | None = None) -> "VoiceOutputGains":
        env = values if values is not None else os.environ
        legacy = env.get("BACKEND_TTS_VOLUME", "1.0")
        cloud = env.get("CLOUD_TTS_OUTPUT_GAIN", legacy)
        local = env.get("LOCAL_TTS_OUTPUT_GAIN", legacy)
        return cls(
            cloud=cls._bounded(cloud, 1.0),
            local=cls._bounded(local, 1.0),
        )
