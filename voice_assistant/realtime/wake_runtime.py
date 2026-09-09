"""Wake-word and supervised chat adapters for the realtime service.

This module keeps wake authorization local and provider-neutral. It is installed
by engine_entry before the realtime service starts, so the core realtime service
remains usable unchanged when WAKE_WORD is disabled.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import time
from typing import Any

from dotenv import dotenv_values

from ..child_command_channel import child_monitor_from_env
from ..semantic_audio import SemanticAudioState
from ..session_context import DEFAULT_CONTEXT_DIR, DEFAULT_SUMMARY_MAX_CHARS, SessionContextStore
from ..wake_logging import format_openwakeword_detected, format_openwakeword_waiting
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


def install(service: Any, env_file: str | Path) -> RealtimeWakeGate | None:
    """Install local wake gating and supervised chat into ``realtime.service``."""
    env_path = Path(env_file).expanduser().resolve()
    values = dict(dotenv_values(env_path))

    def active_session_context_instruction() -> str:
        try:
            size = int(str(values.get("SESSION_CONTEXT_SIZE") or os.getenv("SESSION_CONTEXT_SIZE") or "6000"))
        except (TypeError, ValueError):
            size = 6000
        if size <= 0:
            return ""
        raw_dir = str(values.get("SESSION_CONTEXT_DIR") or os.getenv("SESSION_CONTEXT_DIR") or DEFAULT_CONTEXT_DIR).strip()
        context_dir = Path(raw_dir).expanduser()
        if not context_dir.is_absolute():
            context_dir = Path.cwd() / context_dir
        store = SessionContextStore(context_dir, summary_max_chars=DEFAULT_SUMMARY_MAX_CHARS)
        context = store.context_text(exclude_last_user=True, max_chars=size).strip()
        if not context:
            return ""
        return f"{SESSION_CONTEXT_INSTRUCTION_HEADER}\n{context}"

    def realtime_instructions_with_active_session(engine) -> str:
        # Preserve the fully composed engine prompt: ASSISTANT_SYSTEM_PROMPT,
        # MCP-provided instructions and provider-neutral realtime rules are
        # already in engine.config.instructions. Only append the active session
        # context as an internal, replaceable block for the current turn.
        base = str(getattr(getattr(engine, "config", None), "instructions", "") or "").strip()
        if not base:
            base = str(getattr(service, "DEFAULT_BASE_PROMPT", "") or "").strip()
        context = active_session_context_instruction()
        return f"{base}\n\n{context}" if context else base

    async def refresh_engine_context(engine) -> bool:
        updater = getattr(engine, "update_instructions", None)
        if not callable(updater):
            return False
        await updater(realtime_instructions_with_active_session(engine))
        return True

    original_event_loop = service.event_loop

    async def supervised_text_command_loop(engine, monitor, stop_event, semantic, turn_tracker):
        while not stop_event.is_set():
            try:
                if await asyncio.to_thread(monitor.pop_cancel_requested):
                    try:
                        await engine.cancel_response()
                    except Exception as exc:
                        print(f"Realtime text cancel warning: {exc}", flush=True)
                    turn_tracker.reset_after_cancel_or_failure()
                    monitor.set_assistant_busy(False)
                command = await asyncio.to_thread(monitor.pop_injected_command)
                if command:
                    text = str(command.get("text") or "").strip()
                    if text:
                        print(f"Realtime injected text command: {text}", flush=True)
                        await asyncio.to_thread(monitor.append_dialogue, "user", text, persisted_by_child=False)
                        await asyncio.to_thread(monitor.set_assistant_busy, True)
                        turn_tracker.start_text_turn()
                        semantic.transition(SemanticAudioState.PROCESSING)
                        context_refreshed = await refresh_engine_context(engine)
                        if not context_refreshed:
                            print("Realtime session context refresh unavailable for this provider; sending clean user text only", flush=True)
                        await engine.send_text(text)
            except Exception as exc:
                print(f"Realtime supervised text command warning: {exc}", flush=True)
            await asyncio.sleep(0.15)

    async def event_loop_with_supervised_chat(
        engine,
        bridge,
        queue,
        interrupted,
        first_played,
        stop_event,
        semantic,
        provider_failure,
    ):
        monitor = child_monitor_from_env()
        if monitor is None:
            return await original_event_loop(engine, bridge, queue, interrupted, first_played, stop_event, semantic, provider_failure)

        completed_calls = service._RECENT_BRIDGE_CALL_IDS
        completed_responses: set[str] = set()
        audio_started: set[str] = set()
        tool_tasks: set[asyncio.Task] = set()
        completion_tasks: set[asyncio.Task] = set()
        turn_tracker = service.RealtimeTurnTracker(
            action_grace_seconds=service._float_env(
                "REALTIME_ACTION_GRACE_SECONDS",
                service.DEFAULT_REALTIME_ACTION_GRACE_SECONDS,
            )
        )
        turn = 0
        command_task = asyncio.create_task(
            supervised_text_command_loop(engine, monitor, stop_event, semantic, turn_tracker),
            name="lsa-realtime-supervised-text",
        )
        try:
            while not stop_event.is_set():
                inactivity_timeout = service._float_env(
                    "REALTIME_INACTIVITY_TIMEOUT_SECONDS",
                    service.DEFAULT_REALTIME_INACTIVITY_TIMEOUT_SECONDS,
                )
                try:
                    event = await asyncio.wait_for(
                        engine.next_event(),
                        timeout=inactivity_timeout if inactivity_timeout > 0 else None,
                    )
                except asyncio.TimeoutError:
                    if turn_tracker.has_ambiguous_action():
                        print("Realtime inactivity timeout deferred: action still in progress", flush=True)
                        turn_tracker.touch()
                        continue
                    print(f"Realtime inactivity timeout after {inactivity_timeout:.1f}s; closing session", flush=True)
                    stop_event.set()
                    return
                now = time.perf_counter()
                turn_tracker.touch()
                if event.type == "speech_started":
                    print("Realtime speech started", flush=True)
                    turn_tracker.speech_started()
                    if turn_tracker.current_response_id:
                        interrupted.add(turn_tracker.current_response_id)
                        service.clear_queue(queue)
                        try:
                            await engine.cancel_response()
                        except Exception as exc:
                            print(f"Realtime cancellation warning: {exc}", flush=True)
                        turn_tracker.reset_after_cancel_or_failure()
                elif event.type == "speech_stopped":
                    turn_tracker.speech_stopped(now)
                    print("Realtime speech stopped", flush=True)
                elif event.type == "user_transcript_done":
                    text = str(event.data.get("text") or "").strip()
                    if text:
                        print(f"Utilisateur: {text}", flush=True)
                        await asyncio.to_thread(monitor.append_dialogue, "user", text, persisted_by_child=False)
                        await asyncio.to_thread(monitor.set_assistant_busy, True)
                        turn_tracker.start_text_turn()
                elif event.type == "response_started":
                    response = event.data.get("response") or {}
                    turn_tracker.response_started_event(str(response.get("id") or ""), now)
                    await asyncio.to_thread(monitor.set_assistant_busy, True)
                    semantic.transition(SemanticAudioState.PROCESSING)
                elif event.type == "tool_call" and bridge is not None:
                    await asyncio.to_thread(monitor.set_assistant_busy, True)
                    semantic.transition(SemanticAudioState.PROCESSING)
                    task = asyncio.create_task(service.execute_bridge_call(engine, bridge, event, completed_calls, turn_tracker))
                    tool_tasks.add(task)
                    task.add_done_callback(tool_tasks.discard)
                elif event.type == "tool_followup_requested":
                    turn_tracker.tool_followup_requested()
                    await asyncio.to_thread(monitor.set_assistant_busy, True)
                    semantic.transition(SemanticAudioState.PROCESSING)
                    print("Realtime tool follow-up requested", flush=True)
                elif event.type in {"mcp_list_tools", "mcp_call", "mcp_approval_request", "mcp_event", "mcp_followup_requested"}:
                    print(f"Realtime native MCP event: {event.type}", flush=True)
                elif event.type == "audio_delta":
                    response_id = str(event.data.get("response_id") or turn_tracker.current_response_id)
                    if response_id in interrupted:
                        continue
                    audio = event.data.get("audio") or b""
                    if audio:
                        if response_id not in audio_started:
                            audio_started.add(response_id)
                            semantic.transition(SemanticAudioState.RESULT_READY)
                            semantic.transition(SemanticAudioState.SPEAKING)
                        turn_tracker.audio_started(response_id, now)
                        await queue.put((response_id, audio))
                elif event.type == "transcript_done":
                    text = str(event.data.get("text") or "").strip()
                    if text:
                        print(f"Assistant: {text}", flush=True)
                        await asyncio.to_thread(monitor.append_dialogue, "assistant", text, persisted_by_child=False)
                elif event.type == "response_done":
                    response_id = str(event.data.get("response_id") or turn_tracker.current_response_id)
                    if response_id in completed_responses:
                        continue
                    completed_responses.add(response_id)
                    turn += 1
                    metric_values = turn_tracker.metrics_for(response_id, now)
                    speech_end = turn_tracker.speech_stop_by_response.get(response_id)
                    metrics = {"pipeline":"realtime","provider":engine.config.provider,"model":engine.config.model,"turn":turn,"response_id":response_id,"speech_end_to_first_audio_ms":metric_values["speech_end_to_first_audio_ms"],"speech_end_to_first_playback_ms":round((first_played[response_id]-speech_end)*1000,1) if speech_end is not None and response_id in first_played else None,"response_start_to_done_ms":metric_values["response_start_to_done_ms"],"usage":event.data.get("usage") or {}}
                    metrics["cost_usd"] = service.realtime_usage_cost_usd(engine.config.model, metrics["usage"]) if engine.config.provider == "openai" else None
                    print("REALTIME_METRICS " + json.dumps(metrics, ensure_ascii=False, separators=(",", ":")), flush=True)
                    turn_tracker.response_done(response_id)
                    task = asyncio.create_task(
                        service.settle_completed_response(
                            response_id=response_id,
                            response_had_audio=response_id in audio_started,
                            turn_tracker=turn_tracker,
                            tool_tasks=tool_tasks,
                            queue=queue,
                            semantic=semantic,
                            set_busy=monitor.set_assistant_busy,
                        )
                    )
                    completion_tasks.add(task)
                    task.add_done_callback(completion_tasks.discard)
                elif event.type in {"provider_error", "connection_error"}:
                    if event.type == "provider_error" and service.is_benign_provider_error(event.data):
                        print(f"Realtime provider benign warning: {event.data}", flush=True)
                        continue
                    print(f"Realtime provider error: {event.data}", flush=True)
                    if turn_tracker.has_ambiguous_action():
                        print("Realtime action state reset after provider error; no in-flight action will be replayed automatically", flush=True)
                    turn_tracker.reset_after_cancel_or_failure()
                    await asyncio.to_thread(monitor.set_assistant_busy, False)
                    provider_failure.set()
                elif event.type == "connection_closed":
                    print("Realtime connection closed", flush=True)
                    if turn_tracker.has_ambiguous_action():
                        print("Realtime action state reset after connection close; no in-flight action will be replayed automatically", flush=True)
                    turn_tracker.reset_after_cancel_or_failure()
                    await asyncio.to_thread(monitor.set_assistant_busy, False)
                    provider_failure.set()
                    stop_event.set()
        finally:
            command_task.cancel()
            for task in tuple(tool_tasks):
                task.cancel()
            for task in tuple(completion_tasks):
                task.cancel()
            pending = [command_task, *tool_tasks, *completion_tasks]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    service.event_loop = event_loop_with_supervised_chat

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

        if state == SemanticAudioState.LISTENING and gate.waiting:
            return original_transition(self, SemanticAudioState.WAIT_WAKE)

        if state == SemanticAudioState.SPEAKING:
            state_ref["speaking"] = True
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
