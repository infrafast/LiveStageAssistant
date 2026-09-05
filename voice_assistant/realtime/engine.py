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
class RealtimeMCPServer:
    """Provider-neutral description of one provider-native remote MCP server."""

    label: str
    url: str
    authorization: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    allowed_tools: tuple[str, ...] = ()
    require_approval: str = "never"
    description: str = ""

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("realtime MCP server label is required")
        if not self.url.strip():
            raise ValueError("realtime MCP server URL is required")
        if self.require_approval not in {"always", "never"}:
            raise ValueError("realtime MCP require_approval must be 'always' or 'never'")


@dataclass(frozen=True)
class RealtimeFunctionTool:
    """Provider-neutral function tool exposed to a realtime model.

    RV2B uses this contract for the LSA MCP bridge: MCP tools are discovered by
    the existing LSA MCP client and represented to the realtime provider as
    ordinary function tools. The bridge retains the mapping back to the MCP
    server/tool and owns actual execution.
    """

    name: str
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    context_instructions: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("realtime function tool name is required")
        if not isinstance(self.parameters, dict):
            raise ValueError("realtime function tool parameters must be a JSON-schema object")


@dataclass(frozen=True)
class RealtimeEngineConfig:
    provider: str
    model: str
    voice: str = "marin"
    instructions: str = "Respond naturally and concisely in the user's language. Default to English when unclear."
    server_vad: bool = True
    input_transcription_model: str = "gpt-4o-mini-transcribe"
    mcp_servers: tuple[RealtimeMCPServer, ...] = ()
    function_tools: tuple[RealtimeFunctionTool, ...] = ()

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
    async def send_text(self, text: str, *, create_response: bool = True) -> None:
        """Inject one user text turn, optionally asking the provider to respond."""

    @abstractmethod
    async def commit_audio(self) -> None:
        """Commit buffered input audio when provider-side turn detection is disabled."""

    @abstractmethod
    async def next_event(self) -> RealtimeEvent:
        """Wait for the next provider-neutral realtime event."""

    @abstractmethod
    async def cancel_response(self) -> None:
        """Cancel the current provider response for barge-in/cancellation."""

    @abstractmethod
    async def submit_tool_result(self, call_id: str, result: Any) -> None:
        """Return an existing LSA bridge/tool-path result to the provider session."""
