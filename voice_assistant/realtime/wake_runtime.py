"""Realtime wake-word and supervised WebMonitor callbacks.

The realtime service owns the provider event loop and capture loop. This module
contains only runtime adapters that plug into those loops through
``RealtimeRuntimeCallbacks``.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from ..child_command_channel import child_monitor_from_env
from ..semantic_audio import SemanticAudioController, SemanticAudioState
from ..session_context import DEFAULT_CONTEXT_DIR, DEFAULT_SUMMARY_MAX_CHARS, SessionContextStore
from ..wake_logging import format_openwakeword_detected, format_openwakeword_waiting
from .service import RealtimeRuntimeCallbacks
from .wake_gate import RealtimeWakeConfig, RealtimeWakeGate


SESSION_CONTEXT_INSTRUCTION_HEADER = (
    "Internal active session context. Use silently for continuity, preferences, aliases and follow-up references. "
    "Never acknowledge, thank the user for, quote, summarize, or mention this context unless the user explicitly asks. "
    "Do not treat it as live external state."
)


def _bool(value: object, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _active_session_context_instruction(values: dict[str, Any], *, cwd: Path | None = None) -> str:
    try:
        size = int(str(values.get("SESSION_CONTEXT_SIZE") or os.getenv("SESSION_CONTEXT_SIZE") or "6000"))
    except (TypeError, ValueError):
        size = 6000
    if size <= 0:
        return ""

    raw_dir = str(values.get("SESSION_CONTEXT_DIR") or os.getenv("SESSION_CONTEXT_DIR") or DEFAULT_CONTEXT_DIR).strip()
    context_dir = Path(raw_dir).expanduser()
    if not context_dir.is_absolute():
        context_dir = (cwd or Path.cwd()) / context_dir

    store = SessionContextStore(context_dir, summary_max_chars=DEFAULT_SUMMARY_MAX_CHARS)
    context = store.context_text(exclude_last_user=True, max_chars=size).strip()
    if not context:
        return ""
    return f"{SESSION_CONTEXT_INSTRUCTION_HEADER}\n{context}"


def _realtime_instructions_with_active_session(engine, values: dict[str, Any], *, cwd: Path | None = None) -> str:
    # Preserve the fully composed engine prompt. ASSISTANT_SYSTEM_PROMPT,
    # MCP-provided instructions and provider-neutral realtime rules are already
    # in engine.config.instructions. Only append the current session context as
    # an internal, replaceable block for this turn.
    base = str(getattr(getattr(engine, "config", None), "instructions", "") or "").strip()
    context = _active_session_context_instruction(values, cwd=cwd)
    return f"{base}\n\n{context}" if context else base


class RealtimeWakeRuntime:
    """Local realtime wake gate wired through explicit service callbacks."""

    def __init__(self, config: RealtimeWakeConfig, *, interrupt_enabled: bool = False) -> None:
        self.config = config
        self.gate = RealtimeWakeGate(config)
        self.interrupt_enabled = bool(interrupt_enabled)
        self.semantic: SemanticAudioController | None = None
        self.speaking = False
        print(
            format_openwakeword_waiting(
                config.wake_word,
                threshold=config.threshold,
                model_label=(config.model_names or config.model_paths or ("configured model",))[0],
                engine="realtime",
            ),
            flush=True,
        )
        print(
            "LSA Realtime wake enabled: "
            f"word={config.wake_word} threshold={config.threshold:.2f} "
            f"pre_roll_ms={config.pre_roll_ms} cooldown_ms={config.cooldown_ms} "
            f"post_tts_ms={config.post_tts_suppression_ms} "
            f"interrupt={'on' if self.interrupt_enabled else 'off'}",
            flush=True,
        )

    def semantic_transition(self, semantic: SemanticAudioController, state: SemanticAudioState) -> bool:
        self.semantic = semantic

        if state == SemanticAudioState.LISTENING and self.gate.waiting:
            return bool(semantic.transition(SemanticAudioState.WAIT_WAKE))

        if state == SemanticAudioState.SPEAKING:
            self.speaking = True
            self.gate.rearm(suppress_ms=0)
            return bool(semantic.transition(state))

        if state == SemanticAudioState.IDLE:
            self.speaking = False
            result = bool(semantic.transition(state))
            self.gate.rearm()
            return result

        return bool(semantic.transition(state))

    async def capture_filter(self, engine, realtime_pcm: bytes, stop_event: asyncio.Event) -> bool:
        if not self.gate.waiting:
            return False

        if self.speaking and not self.interrupt_enabled:
            return True

        detected = self.gate.feed(realtime_pcm)
        if not detected:
            return True

        if self.semantic is not None:
            self.semantic_transition(self.semantic, SemanticAudioState.WAKE_DETECTED)

        pre_roll = self.gate.consume_pre_roll()
        print(
            format_openwakeword_detected(
                self.config.wake_word,
                label=self.gate.last_detection_label
                or (self.config.model_names or self.config.model_paths or ("configured model",))[0],
                score=self.gate.last_detection_score,
                threshold=self.config.threshold,
                engine="realtime",
                detail=f"pre_roll_bytes={len(pre_roll)}",
            ),
            flush=True,
        )
        if pre_roll:
            try:
                await engine.send_audio(pre_roll)
            except Exception as exc:
                print(f"Realtime send error: {exc}", flush=True)
                stop_event.set()
        return True


def build_runtime_callbacks(env_file: str | Path) -> RealtimeRuntimeCallbacks:
    """Create explicit callbacks for the provider-neutral realtime service."""
    env_path = Path(env_file).expanduser().resolve()
    values = dict(dotenv_values(env_path))
    callbacks = RealtimeRuntimeCallbacks()

    monitor = child_monitor_from_env()
    if monitor is not None:
        callbacks.append_dialogue = lambda role, text: monitor.append_dialogue(
            role,
            text,
            persisted_by_child=False,
        )
        callbacks.set_busy = monitor.set_assistant_busy
        callbacks.pop_cancel_requested = monitor.pop_cancel_requested
        callbacks.pop_injected_command = monitor.pop_injected_command

        async def refresh_engine_context(engine) -> bool:
            updater = getattr(engine, "update_instructions", None)
            if not callable(updater):
                return False
            await updater(_realtime_instructions_with_active_session(engine, values, cwd=Path.cwd()))
            return True

        callbacks.refresh_engine_context = refresh_engine_context

    config = RealtimeWakeConfig.from_env(values)
    if not config.enabled:
        print("LSA Realtime wake: disabled", flush=True)
        return callbacks

    runtime = RealtimeWakeRuntime(
        config,
        interrupt_enabled=_bool(values.get("INTERRUPT_CONVERSATION_ENABLED"), False),
    )
    callbacks.capture_filter = runtime.capture_filter
    callbacks.semantic_transition = runtime.semantic_transition
    return callbacks
