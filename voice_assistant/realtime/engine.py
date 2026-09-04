"""Provider-neutral realtime engine contract introduced by RV0.

RV0 deliberately contains no OpenAI, Gemini, WebSocket or WebRTC implementation.
Provider transports start in RV1 and must implement this boundary without altering
the existing classic or MCP execution paths.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any


class RealtimeEngineState(str, Enum):
    STOPPED = "stopped"
    READY = "ready"
    ACTIVE = "active"
    ERROR = "error"


@dataclass(frozen=True)
class RealtimeEngineConfig:
    provider: str
    model: str

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("realtime provider is required")
        if not self.model.strip():
            raise ValueError("realtime model is required")


class RealtimeEngine(ABC):
    """Minimal lifecycle contract shared by future realtime providers."""

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
        """Send one backend audio chunk to the active realtime provider."""

    @abstractmethod
    async def cancel_response(self) -> None:
        """Cancel the current provider response for barge-in/cancellation."""

    @abstractmethod
    async def submit_tool_result(self, call_id: str, result: Any) -> None:
        """Return an existing LSA tool-path result to the provider session."""
