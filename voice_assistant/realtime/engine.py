"""Provider-neutral realtime engine contract for realtime voice providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RealtimeEngineState(str, Enum):
    STOPPED = "stopped"
    CONNECTING = "connecting"
    READY = "ready"
    ACTIVE = "active"
    ERROR = "error"


@dataclass(frozen=True)
class RealtimeEngineConfig:
    provider: str
    model: str
    voice: str = "marin"
    instructions: str = "Réponds en français, de façon concise et naturelle."
    server_vad: bool = True

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("realtime provider is required")
        if not self.model.strip():
            raise ValueError("realtime model is required")
        if not self.voice.strip():
            raise ValueError("realtime voice is required")


@dataclass(frozen=True)
class RealtimeEvent:
    """Provider-neutral event emitted by a realtime engine."""

    type: str
    data: dict[str, Any] = field(default_factory=dict)


class RealtimeEngine(ABC):
    """Lifecycle and event contract shared by realtime provider adapters."""

    def __init__(self, config: RealtimeEngineConfig) -> None:
        self.config = config
        self.state = RealtimeEngineState.STOPPED

    @abstractmethod
    async def start(self) -> None:
        """Prepare the provider session and move to READY."""

    @abstractmethod
    async def stop(self) -> None:
        """Close provider/audio resources and move to STOPPED."""

    @abstractmethod
    async def send_audio(self, pcm: bytes) -> None:
        """Send one 24 kHz mono PCM16 audio chunk to the active provider."""

    @abstractmethod
    async def next_event(self) -> RealtimeEvent:
        """Wait for the next provider-neutral realtime event."""

    @abstractmethod
    async def cancel_response(self) -> None:
        """Cancel the current provider response for barge-in/cancellation."""

    @abstractmethod
    async def submit_tool_result(self, call_id: str, result: Any) -> None:
        """Return an existing LSA tool-path result to the provider session."""
