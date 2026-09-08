"""Wake-word lifecycle adapter for the existing realtime service.

This module keeps wake authorization local and provider-neutral. It is installed
by engine_entry before the realtime service starts, so the core realtime service
remains usable unchanged when WAKE_WORD is disabled.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from ..semantic_audio import SemanticAudioState
from ..wake_logging import format_openwakeword_detected, format_openwakeword_waiting
from .wake_gate import RealtimeWakeConfig, RealtimeWakeGate


def _bool(value: object, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def install(service: Any, env_file: str | Path) -> RealtimeWakeGate | None:
    """Install local wake gating into ``realtime.service`` when configured."""
    values = dict(dotenv_values(Path(env_file).expanduser().resolve()))
    config = RealtimeWakeConfig.from_env(values)
    if not config.enabled:
        print("LSA Realtime wake: disabled", flush=True)
        return None

    gate = RealtimeWakeGate(config)
    interrupt_enabled = _bool(values.get("INTERRUPT_CONVERSATION_ENABLED"), False)
    semantic_ref: dict[str, Any] = {"controller": None}
    state_ref = {"speaking": False}
    print(
        format_openwakeword_waiting(
            config.wake_word,
            threshold=config.threshold,
            model_label=(config.model_names or config.model_paths or ("configured model",))[0],
            engine="realtime",
        ),
        flush=True,
    )

    original_transition = service.SemanticAudioController.transition

    def transition(self, state):
        semantic_ref["controller"] = self

        # Direct LISTENING is valid only when wake is disabled or after a valid
        # wake has already authorized the current command.
        if state == SemanticAudioState.LISTENING and gate.waiting:
            return original_transition(self, SemanticAudioState.WAIT_WAKE)

        if state == SemanticAudioState.SPEAKING:
            state_ref["speaking"] = True
            # During assistant speech, ambient audio must not reach the provider.
            # With interruption enabled, wake detection remains locally active;
            # otherwise all mic audio is ignored until speech completes.
            gate.rearm(suppress_ms=0)
            return original_transition(self, state)

        if state == SemanticAudioState.IDLE:
            state_ref["speaking"] = False
            result = original_transition(self, state)
            gate.rearm()
            return result

        return original_transition(self, state)

    service.SemanticAudioController.transition = transition

    async def gated_capture_loop(engine, stream, source_rate, channels, frames, stop_event):
        resampler = service.Pcm16MonoResampler(source_rate, service.REALTIME_RATE)
        while not stop_event.is_set():
            try:
                pcm = await asyncio.to_thread(stream.read, frames, False)
            except Exception as exc:
                print(f"Realtime input error: {exc}", flush=True)
                stop_event.set()
                return

            realtime_pcm = resampler.process(service.downmix_pcm16(pcm, channels))
            if not realtime_pcm:
                continue

            if gate.waiting:
                if state_ref["speaking"] and not interrupt_enabled:
                    continue
                detected = gate.feed(realtime_pcm)
                if detected:
                    controller = semantic_ref.get("controller")
                    if controller is not None:
                        original_transition(controller, SemanticAudioState.WAKE_DETECTED)
                    pre_roll = gate.consume_pre_roll()
                    print(
                        format_openwakeword_detected(
                            config.wake_word,
                            label=gate.last_detection_label or (config.model_names or config.model_paths or ("configured model",))[0],
                            score=gate.last_detection_score,
                            threshold=config.threshold,
                            engine="realtime",
                            detail=f"pre_roll_bytes={len(pre_roll)}",
                        ),
                        flush=True,
                    )
                    # Preserve the phrase around the wake boundary so commands
                    # such as "momo monte le clic" can remain a single utterance.
                    # The provider may receive the wake word itself; wake remains
                    # authorization metadata, not a provider-side security gate.
                    if pre_roll:
                        try:
                            await engine.send_audio(pre_roll)
                        except Exception as exc:
                            print(f"Realtime send error: {exc}", flush=True)
                            stop_event.set()
                            return
                continue

            try:
                await engine.send_audio(realtime_pcm)
            except Exception as exc:
                print(f"Realtime send error: {exc}", flush=True)
                stop_event.set()
                return

    service.capture_loop = gated_capture_loop
    print(
        "LSA Realtime wake enabled: "
        f"word={config.wake_word} threshold={config.threshold:.2f} "
        f"pre_roll_ms={config.pre_roll_ms} cooldown_ms={config.cooldown_ms} "
        f"post_tts_ms={config.post_tts_suppression_ms} "
        f"interrupt={'on' if interrupt_enabled else 'off'}",
        flush=True,
    )
    return gate
