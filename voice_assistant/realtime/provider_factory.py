"""Provider-neutral construction of realtime engines."""

from __future__ import annotations

from .engine import RealtimeEngine, RealtimeEngineConfig
from .gemini_live import GeminiLiveEngine
from .openai_realtime import OpenAIRealtimeEngine


def create_realtime_engine(
    provider: str,
    config: RealtimeEngineConfig,
    *,
    api_key: str,
) -> RealtimeEngine:
    name = str(provider or "").strip().lower()
    if name == "openai":
        return OpenAIRealtimeEngine(config, api_key=api_key)
    if name == "gemini":
        if config.mcp_servers:
            raise ValueError("Gemini Live uses the LSA function-tool bridge; provider-native MCP is not supported by this adapter")
        return GeminiLiveEngine(config, api_key=api_key)
    raise ValueError(f"unsupported realtime provider: {provider!r}")
