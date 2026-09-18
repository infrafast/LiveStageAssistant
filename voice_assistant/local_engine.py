"""Dedicated deterministic Local voice engine.

The class reuses VoiceAssistant's mature local audio/STT/TTS/wake pipeline but
replaces every LLM/MCPAgent entry point with the versioned deterministic MCP
command-gateway orchestrator.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from . import agent
from . import classic_engine
from .local_gateway_runtime import DeterministicGatewayOrchestrator


class DeterministicLocalVoiceAssistant(agent.VoiceAssistant):
    """VoiceAssistant speech shell with no generative model or MCPAgent."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.local_gateway_orchestrator: DeterministicGatewayOrchestrator | None = None
        # Explicit non-LLM identity used by logs/diagnostics.
        self.llm_provider = "local"
        self.model = "deterministic"
        self.agent = None

    def _build_llm(self):
        raise RuntimeError("Deterministic Local engine must never construct an LLM")

    async def refresh_session_llm_summary(self, *, force: bool = False) -> bool:
        """Local mode never invokes a model to summarize conversation state."""
        return False

    async def initialize_mcp(self):
        """Open gateway-capable MCP sessions without constructing MCPAgent."""
        config = self.mcp_config or {"mcpServers": {}}
        self.mcp_initialization_error = None
        self.mcp_failed_servers = {}

        try:
            orchestrator = DeterministicGatewayOrchestrator(
                config,
                locale=self.stt_language or "fr",
            )
            discovered = await orchestrator.start()
            self.local_gateway_orchestrator = orchestrator
            self.mcp_client = orchestrator.client
            # Startup messaging expects a tool count. Count only the two
            # reserved gateway tools per capable MCP, not ordinary cloud tools.
            self.mcp_all_tools = [object() for _ in range(len(discovered) * 2)]
            if not discovered:
                self.mcp_initialization_error = (
                    "No MCP server exposes the compatible deterministic Local gateway"
                )
                print(
                    "LSA Local degraded: no compatible deterministic MCP gateway is available.",
                    flush=True,
                )
                return False
            print(
                "LSA Local MCP ready: " + ", ".join(discovered),
                flush=True,
            )
            return True
        except Exception as exc:
            self.mcp_initialization_error = str(exc)
            print(f"LSA Local MCP initialization failed: {exc}", flush=True)
            return False

    async def announce_startup_ready(self, loaded_servers: list[str]) -> None:
        print("LSA Local ready: deterministic", flush=True)
        await super().announce_startup_ready(loaded_servers)

    async def process_command(self, text: str, speaker_result=None) -> str:
        """Process a transcript only through deterministic MCP gateways."""
        print(f"\nYou said: {text}")
        if self.session_context_store:
            self.session_context_store.append_message("user", text)
            if self.web_monitor:
                self.web_monitor.set_context_state(
                    self.session_context_store.snapshot(),
                    session_context_size=self.session_context_size,
                )
        if self.web_monitor:
            self.web_monitor.append_dialogue("user", text)
            self.web_monitor.set_assistant_busy(True)

        normalized = text.strip().casefold()
        if normalized in {"exit", "quit", "goodbye"}:
            return "Au revoir."
        if self.is_voice_cancel_phrase(text):
            self.stop_tts()
            return "D'accord, j'arrête."
        if normalized == "clear":
            if self.session_context_store:
                self.session_context_store.clear_current()
                if self.web_monitor:
                    self.web_monitor.replace_dialogue(
                        self.session_context_store.snapshot().get("messages") or []
                    )
            return "Historique local effacé."
        if self.is_speaker_identity_query(text):
            return self.voice_detected_response(speaker_result)

        orchestrator = self.local_gateway_orchestrator
        if not orchestrator or not orchestrator.servers:
            detail = self.mcp_initialization_error or "aucun gateway compatible"
            return f"Mode Local disponible, mais aucune commande MCP déterministe n'est disponible. Détail : {detail}"

        self.semantic_audio.transition(agent.SemanticAudioState.PROCESSING)
        try:
            return await orchestrator.handle(text)
        except asyncio.CancelledError:
            self.semantic_audio.transition(agent.SemanticAudioState.IDLE)
            raise
        except Exception as exc:
            print(f"LSA Local deterministic command failed: {exc}", flush=True)
            return "La commande locale n'a pas pu être exécutée."


def build_assistant(env_file: str | Path) -> DeterministicLocalVoiceAssistant:
    """Build the Local speech shell without selecting or constructing an LLM."""
    assistant = classic_engine.build_assistant(
        env_file,
        assistant_class_override=DeterministicLocalVoiceAssistant,
        llm_provider_override="local",
        model_override="deterministic",
    )
    if not isinstance(assistant, DeterministicLocalVoiceAssistant):
        raise RuntimeError("Local engine factory returned an unexpected assistant type")
    return assistant


async def run_async(env_file: str | Path) -> int:
    assistant = build_assistant(env_file)
    result = await assistant.run()
    return 0 if result in {None, "exit", "reload"} else 1


def run(env_file: str | Path) -> int:
    return asyncio.run(run_async(env_file))
