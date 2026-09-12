#!/usr/bin/env python3
"""Integrated provider-neutral Realtime runtime for LiveStageAssistant."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from typing import Any, Awaitable, Callable

import pyaudio
from dotenv import load_dotenv

from ..semantic_audio import (
    SemanticAudioConfig,
    SemanticAudioController,
    SemanticAudioState,
    VoiceOutputGains,
)
from ..semantic_audio_output import SemanticCuePlayer
from ..startup_messages import startup_connectivity_message, startup_ready_message
from ..wake_word import get_configured_wake_words
from .audio import Pcm16MonoResampler, apply_pcm16_gain, downmix_pcm16, expand_pcm16_channels
from .audio_devices import PipeWireInputStream, PipeWireOutputStream, parse_pipewire_selector
from .engine import RealtimeEngineConfig, RealtimeMCPServer
from .mcp_auto import classify_auto_fallback
from .mcp_bridge import RealtimeMCPBridge, load_remote_mcp_prompt
from .mcp_config import CanonicalMCPServerConfig, load_mcp_inventory
from .metrics import realtime_usage_cost_usd
from .provider_factory import create_realtime_engine
from .prompts import compose_realtime_instructions

ROOT = Path(__file__).resolve().parents[2]
REALTIME_RATE = 24000
DEFAULT_MODEL = "gpt-realtime-2.1"
DEFAULT_VOICE = "marin"
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-live-preview"
DEFAULT_GEMINI_VOICE = "Kore"
TRANSIENT_PROVIDER_EXIT = 75
_RECENT_BRIDGE_CALL_IDS: set[str] = set()
DEFAULT_REALTIME_INACTIVITY_TIMEOUT_SECONDS = 0.0
DEFAULT_REALTIME_ACTION_GRACE_SECONDS = 2.5
DEFAULT_REALTIME_TURN_SETTLE_SECONDS = 0.35
DEFAULT_REALTIME_TURN_TIMEOUT_SECONDS = 20.0
DEFAULT_REALTIME_EVENT_POLL_SECONDS = 0.5


def _float_env(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name, str(default)) or str(default)).strip())
    except (TypeError, ValueError):
        return default


@dataclass
class RealtimeRuntimeCallbacks:
    """Optional integration points owned by the parent runtime.

    The realtime service owns the event loop. WebMonitor/session-context/wake-word
    adapters plug into that loop through these explicit callbacks.
    """

    capture_filter: Callable[[Any, bytes, asyncio.Event], Awaitable[bool]] | None = None
    semantic_transition: Callable[[SemanticAudioController, SemanticAudioState], bool] | None = None
    append_dialogue: Callable[[str, str], None] | None = None
    set_busy: Callable[[bool], None] | None = None
    pop_cancel_requested: Callable[[], bool] | None = None
    pop_injected_command: Callable[[], dict[str, Any] | None] | None = None
    refresh_engine_context: Callable[[Any], Awaitable[bool]] | None = None
    should_ignore_provider_speech_started: Callable[[SemanticAudioState | None], bool] | None = None

    @property
    def has_supervised_text(self) -> bool:
        return bool(self.pop_cancel_requested or self.pop_injected_command)


class RealtimeTurnTracker:
    """Track one realtime turn so recovery never has to guess action state."""

    def __init__(self, *, action_grace_seconds: float = DEFAULT_REALTIME_ACTION_GRACE_SECONDS) -> None:
        self.action_grace_seconds = max(0.0, float(action_grace_seconds))
        self.current_response_id = ""
        self.last_activity = time.monotonic()
        self.awaiting_response = False
        self.tool_in_flight = False
        self.speech_started_at: float | None = None
        self.speech_stopped_at: float | None = None
        self.response_started: dict[str, float] = {}
        self.speech_stop_by_response: dict[str, float] = {}
        self.first_audio_received: dict[str, float] = {}

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    def start_text_turn(self) -> None:
        self.awaiting_response = True
        self.touch()

    def speech_started(self) -> None:
        self.speech_started_at = time.perf_counter()
        self.awaiting_response = True
        self.touch()

    def speech_stopped(self, now: float) -> None:
        self.speech_stopped_at = now
        self.touch()

    def response_started_event(self, response_id: str, now: float) -> None:
        self.current_response_id = response_id
        self.awaiting_response = True
        if response_id:
            self.response_started[response_id] = now
            if self.speech_stopped_at is not None:
                self.speech_stop_by_response[response_id] = self.speech_stopped_at
        self.touch()

    def tool_started(self) -> None:
        self.tool_in_flight = True
        self.awaiting_response = True
        self.touch()

    def tool_finished(self, *, expect_followup: bool = True) -> None:
        self.tool_in_flight = False
        self.awaiting_response = bool(expect_followup)
        self.touch()

    def tool_followup_requested(self) -> None:
        self.awaiting_response = True
        self.touch()

    def audio_started(self, response_id: str, now: float) -> None:
        if response_id:
            self.first_audio_received.setdefault(response_id, now)
        self.touch()

    def response_done(self, response_id: str) -> None:
        if self.current_response_id == response_id:
            self.current_response_id = ""
        self.awaiting_response = False
        self.touch()

    def reset_after_cancel_or_failure(self) -> None:
        self.current_response_id = ""
        self.awaiting_response = False
        self.tool_in_flight = False
        self.speech_started_at = None
        self.speech_stopped_at = None
        self.touch()

    def has_pending_work(self) -> bool:
        return bool(
            self.current_response_id
            or self.awaiting_response
            or self.tool_in_flight
        )

    def age_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.last_activity)

    def pending_summary(self) -> str:
        parts: list[str] = []
        if self.current_response_id:
            parts.append(f"response={self.current_response_id}")
        if self.awaiting_response:
            parts.append("awaiting_response")
        if self.tool_in_flight:
            parts.append("tool_in_flight")
        return ", ".join(parts) if parts else "none"

    def has_ambiguous_action(self) -> bool:
        if self.tool_in_flight:
            return True
        if self.current_response_id or self.awaiting_response:
            return True
        if self.speech_stopped_at is None:
            return False
        return time.perf_counter() - self.speech_stopped_at < self.action_grace_seconds

    def metrics_for(self, response_id: str, now: float) -> dict[str, float | None]:
        speech_end = self.speech_stop_by_response.get(response_id)
        first_audio = self.first_audio_received.get(response_id)
        started = self.response_started.get(response_id)
        return {
            "speech_end_to_first_audio_ms": round((first_audio - speech_end) * 1000, 1)
            if speech_end is not None and first_audio is not None
            else None,
            "response_start_to_done_ms": round((now - started) * 1000, 1) if started is not None else None,
        }


def is_benign_provider_error(event_data: dict[str, Any]) -> bool:
    error = event_data.get("error") if isinstance(event_data, dict) else None
    if not isinstance(error, dict):
        return False
    code = str(error.get("code") or "").strip()
    message = str(error.get("message") or "").strip().lower()
    return code == "response_cancel_not_active" or "cancellation failed: no active response found" in message


def _callbacks(callbacks: RealtimeRuntimeCallbacks | None) -> RealtimeRuntimeCallbacks:
    return callbacks if callbacks is not None else RealtimeRuntimeCallbacks()


def transition_semantic(
    semantic: SemanticAudioController,
    state: SemanticAudioState,
    callbacks: RealtimeRuntimeCallbacks | None = None,
) -> bool:
    runtime_callbacks = _callbacks(callbacks)
    if runtime_callbacks.semantic_transition is not None:
        try:
            return bool(runtime_callbacks.semantic_transition(semantic, state))
        except Exception as exc:
            print(f"Realtime semantic callback warning: {exc}", flush=True)
    return bool(semantic.transition(state))


async def _set_busy(callbacks: RealtimeRuntimeCallbacks | None, busy: bool) -> None:
    runtime_callbacks = _callbacks(callbacks)
    if runtime_callbacks.set_busy is None:
        return
    try:
        await asyncio.to_thread(runtime_callbacks.set_busy, busy)
    except Exception as exc:
        print(f"Realtime busy callback warning: {exc}", flush=True)


async def _append_dialogue(callbacks: RealtimeRuntimeCallbacks | None, role: str, text: str) -> None:
    runtime_callbacks = _callbacks(callbacks)
    if runtime_callbacks.append_dialogue is None:
        return
    try:
        await asyncio.to_thread(runtime_callbacks.append_dialogue, role, text)
    except Exception as exc:
        print(f"Realtime dialogue callback warning: {exc}", flush=True)


async def _pop_cancel_requested(callbacks: RealtimeRuntimeCallbacks | None) -> bool:
    runtime_callbacks = _callbacks(callbacks)
    if runtime_callbacks.pop_cancel_requested is None:
        return False
    try:
        return bool(await asyncio.to_thread(runtime_callbacks.pop_cancel_requested))
    except Exception as exc:
        print(f"Realtime cancel callback warning: {exc}", flush=True)
        return False


async def _pop_injected_command(callbacks: RealtimeRuntimeCallbacks | None) -> dict[str, Any] | None:
    runtime_callbacks = _callbacks(callbacks)
    if runtime_callbacks.pop_injected_command is None:
        return None
    try:
        command = await asyncio.to_thread(runtime_callbacks.pop_injected_command)
    except Exception as exc:
        print(f"Realtime command callback warning: {exc}", flush=True)
        return None
    return command if isinstance(command, dict) else None


async def _refresh_engine_context(callbacks: RealtimeRuntimeCallbacks | None, engine) -> bool:
    runtime_callbacks = _callbacks(callbacks)
    if runtime_callbacks.refresh_engine_context is None:
        return False
    try:
        return bool(await runtime_callbacks.refresh_engine_context(engine))
    except Exception as exc:
        print(f"Realtime session context refresh warning: {exc}", flush=True)
        return False


def _should_ignore_provider_speech_started(
    callbacks: RealtimeRuntimeCallbacks | None,
    semantic_state: SemanticAudioState | None,
) -> bool:
    runtime_callbacks = _callbacks(callbacks)
    if runtime_callbacks.should_ignore_provider_speech_started is None:
        return False
    try:
        return bool(runtime_callbacks.should_ignore_provider_speech_started(semantic_state))
    except Exception as exc:
        print(f"Realtime speech-start callback warning: {exc}", flush=True)
        return False


def _active_task_exists(tasks: set[asyncio.Task]) -> bool:
    return any(not task.done() for task in tasks)


def _event_wait_timeout(turn_tracker: RealtimeTurnTracker, tool_tasks: set[asyncio.Task]) -> float | None:
    inactivity_timeout = _float_env("REALTIME_INACTIVITY_TIMEOUT_SECONDS", DEFAULT_REALTIME_INACTIVITY_TIMEOUT_SECONDS)
    if turn_tracker.has_pending_work() or _active_task_exists(tool_tasks):
        poll_seconds = max(0.05, _float_env("REALTIME_EVENT_POLL_SECONDS", DEFAULT_REALTIME_EVENT_POLL_SECONDS))
        return min(poll_seconds, inactivity_timeout) if inactivity_timeout > 0 else poll_seconds
    return inactivity_timeout if inactivity_timeout > 0 else None


def _format_user_transcript_error(data: dict[str, Any]) -> str:
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        message = str(error.get("message") or error.get("code") or error.get("type") or "").strip()
        if message:
            return message
    return str(error or data or "unknown transcription error").strip()


async def handle_turn_watchdog_timeout(
    *,
    engine,
    turn_tracker: RealtimeTurnTracker,
    interrupted: set[str],
    queue: asyncio.Queue,
    semantic: SemanticAudioController,
    callbacks: RealtimeRuntimeCallbacks | None,
    stop_event: asyncio.Event,
    provider_failure: asyncio.Event,
    timeout_seconds: float,
) -> None:
    response_id = turn_tracker.current_response_id
    if response_id:
        interrupted.add(response_id)
    clear_queue(queue)
    print(
        "Realtime turn watchdog timeout after "
        f"{timeout_seconds:.1f}s; pending={turn_tracker.pending_summary()}; reconnecting session",
        flush=True,
    )
    try:
        await engine.cancel_response()
    except Exception as exc:
        print(f"Realtime watchdog cancellation warning: {exc}", flush=True)
    turn_tracker.reset_after_cancel_or_failure()
    transition_semantic(semantic, SemanticAudioState.IDLE, callbacks)
    transition_semantic(semantic, SemanticAudioState.LISTENING, callbacks)
    await _set_busy(callbacks, False)
    provider_failure.set()
    stop_event.set()


async def settle_completed_response(
    *,
    response_id: str,
    response_had_audio: bool,
    turn_tracker: RealtimeTurnTracker,
    tool_tasks: set[asyncio.Task],
    queue: asyncio.Queue,
    semantic: SemanticAudioController,
    callbacks: RealtimeRuntimeCallbacks | None = None,
) -> bool:
    settle_seconds = _float_env("REALTIME_TURN_SETTLE_SECONDS", DEFAULT_REALTIME_TURN_SETTLE_SECONDS)
    if settle_seconds > 0:
        await asyncio.sleep(settle_seconds)
    if turn_tracker.has_pending_work() or any(not task.done() for task in tool_tasks):
        print(f"Realtime turn completion deferred: pending={turn_tracker.pending_summary()}", flush=True)
        return False
    if response_had_audio:
        await queue.put((response_id, None))
    else:
        transition_semantic(semantic, SemanticAudioState.IDLE, callbacks)
        transition_semantic(semantic, SemanticAudioState.LISTENING, callbacks)
    await _set_busy(callbacks, False)
    return True


def read_secret(name: str, env_file: Path) -> str:
    direct = (os.getenv(name) or "").strip()
    if direct:
        return direct
    filename = (os.getenv(f"{name}_FILE") or "").strip()
    if not filename:
        return ""
    path = Path(filename).expanduser()
    if not path.is_absolute():
        candidates = [env_file.parent / path, ROOT / path]
        path = next((item for item in candidates if item.is_file()), candidates[0])
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def resolve_path(value: str, env_file: Path) -> Path:
    path = Path(os.path.expandvars(value)).expanduser()
    if path.is_absolute():
        return path
    candidates = [env_file.parent / path, ROOT / path, ROOT / "assets" / path]
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[-1])


def _bool_env(name: str, default: bool = False) -> bool:
    value = str(os.getenv(name, "true" if default else "false")).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _mcp_prompts_enabled() -> bool:
    return str(os.getenv("MCP_LOAD_SERVER_PROMPT", "true")).strip().lower() not in {"0", "false", "no", "off"}


def _describe_mcp_prompt_source(server_name: str) -> str:
    return f"server='{server_name}' prompt='agent_prompt' tool='get_agent_prompt'"


def _describe_mcp_prompt_sources(server_names: list[str]) -> str:
    return "; ".join(_describe_mcp_prompt_source(name) for name in server_names)


def _format_loaded_mcp_prompts(loaded_prompts: list[tuple[str, str]]) -> str:
    sections: list[str] = []
    for server_name, text in loaded_prompts:
        prompt = str(text or "").strip()
        if prompt:
            sections.append(f'Instructions loaded from MCP server "{server_name}":\n{prompt}')
    return "\n\n".join(sections)


def _log_loaded_mcp_prompts(server_names: list[str], loaded_prompts: list[tuple[str, str]]) -> None:
    if not _mcp_prompts_enabled():
        return
    if server_names:
        print(
            "MCP startup prompt loading enabled. Requested source(s): "
            f"{_describe_mcp_prompt_sources(server_names)}",
            flush=True,
        )
    else:
        print("⚠️ Warning: MCP_LOAD_SERVER_PROMPT is true but no MCP prompt sources are configured.", flush=True)
    if not loaded_prompts:
        print("⚠️ Warning: No MCP server instructions were loaded; keeping the local system prompt.", flush=True)
        return
    loaded_summary = "; ".join(f"{server} via startup prompt" for server, _text in loaded_prompts)
    print(
        f"Loaded and merged {len(loaded_prompts)} MCP prompt source(s) "
        f"with merge mode 'append': {loaded_summary}",
        flush=True,
    )


def play_startup_sound(env_file: Path) -> None:
    if not _bool_env("STARTUP_LOADER_SOUND_ENABLED", True):
        return
    configured = str(os.getenv("STARTUP_LOADER_SOUND_FILE") or "").strip()
    if not configured:
        return
    path = resolve_path(configured, env_file)
    if not path.is_file():
        print(f"Realtime startup sound: not found ({path})", flush=True)
        return
    selected = str(os.getenv("BACKEND_AUDIO_OUTPUT_DEVICE") or "").strip()
    target = parse_pipewire_selector(selected, kind="sink")
    try:
        if shutil.which("pw-play"):
            command = ["pw-play"]
            if target:
                command += ["--target", target]
            command.append(str(path))
        elif shutil.which("aplay"):
            command = ["aplay", "-q", str(path)]
        else:
            print("Realtime startup sound: no pw-play/aplay available", flush=True)
            return
        print(f"Realtime startup sound: {path}", flush=True)
        subprocess.run(command, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
    except Exception as exc:
        print(f"Realtime startup sound: failed ({exc})", flush=True)


def resolve_device_index(pa: pyaudio.PyAudio, value: str, *, input_device: bool) -> int | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return int(value.split(":", 1)[0])
    except ValueError:
        pass
    key = "maxInputChannels" if input_device else "maxOutputChannels"
    matches: list[int] = []
    for index in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(index)
        if int(info.get(key) or 0) <= 0:
            continue
        name = str(info.get("name") or "")
        if value.casefold() == name.casefold():
            return index
        if value.casefold() in name.casefold() or name.casefold() in value.casefold():
            matches.append(index)
    if len(matches) == 1:
        return matches[0]
    raise RuntimeError(f"could not map configured audio device {value!r}")


def _device_info(pa: pyaudio.PyAudio, index: int | None, *, input_device: bool) -> dict:
    if index is not None:
        return pa.get_device_info_by_index(index)
    return pa.get_default_input_device_info() if input_device else pa.get_default_output_device_info()


def _unique_ints(values) -> list[int]:
    result: list[int] = []
    for value in values:
        try:
            item = int(round(float(value)))
        except (TypeError, ValueError):
            continue
        if item > 0 and item not in result:
            result.append(item)
    return result


def open_configured_input(pa: pyaudio.PyAudio, selected: str):
    target = parse_pipewire_selector(selected, kind="source")
    if target:
        stream = PipeWireInputStream(target, rate=REALTIME_RATE, channels=1, chunk=480)
        return stream, REALTIME_RATE, 1, 480, f"PipeWire source {target}"
    index = resolve_device_index(pa, selected, input_device=True)
    info = _device_info(pa, index, input_device=True)
    max_channels = max(1, int(info.get("maxInputChannels") or 1))
    errors: list[str] = []
    for rate in _unique_ints([REALTIME_RATE, info.get("defaultSampleRate"), 48000, 44100, 16000]):
        frames = max(160, int(rate * 0.02))
        for channels in ([1, 2] if max_channels >= 2 else [1]):
            try:
                stream = pa.open(format=pyaudio.paInt16, channels=channels, rate=rate, input=True, input_device_index=index, frames_per_buffer=frames)
                return stream, rate, channels, frames, str(info.get("name") or index or "default")
            except Exception as exc:
                errors.append(f"{channels}ch/{rate}: {exc}")
    raise RuntimeError("could not open realtime input: " + "; ".join(errors[-4:]))


def open_configured_output(pa: pyaudio.PyAudio, selected: str):
    target = parse_pipewire_selector(selected, kind="sink")
    if target:
        stream = PipeWireOutputStream(target, rate=REALTIME_RATE, channels=1)
        return stream, REALTIME_RATE, 1, f"PipeWire sink {target}"
    index = resolve_device_index(pa, selected, input_device=False)
    info = _device_info(pa, index, input_device=False)
    max_channels = max(1, int(info.get("maxOutputChannels") or 1))
    errors: list[str] = []
    for rate in _unique_ints([REALTIME_RATE, info.get("defaultSampleRate"), 48000, 44100]):
        for channels in ([1, 2] if max_channels >= 2 else [1]):
            try:
                stream = pa.open(format=pyaudio.paInt16, channels=channels, rate=rate, output=True, input_device_index=None, output_device_index=index, frames_per_buffer=max(160, int(rate * 0.02)))
                return stream, rate, channels, str(info.get("name") or index or "default")
            except Exception as exc:
                errors.append(f"{channels}ch/{rate}: {exc}")
    raise RuntimeError("could not open realtime output: " + "; ".join(errors[-4:]))


def _split_authorization(headers: dict[str, str]) -> tuple[str, dict[str, str]]:
    clean = dict(headers)
    authorization = ""
    for key in list(clean):
        if key.casefold() != "authorization":
            continue
        value = str(clean.pop(key) or "").strip()
        authorization = value[7:].strip() if value.casefold().startswith("bearer ") else value
        break
    return authorization, clean


async def native_server(server: CanonicalMCPServerConfig, *, strict_probe: bool = False) -> RealtimeMCPServer:
    if not server.native.url.lower().startswith("https://"):
        raise RuntimeError(f"MCP {server.name!r} native transport requires an HTTPS URL")
    authorization, headers = _split_authorization(server.native.headers)
    prompt = ""
    if _mcp_prompts_enabled():
        try:
            prompt = await load_remote_mcp_prompt(server_name=server.name, url=server.native.url, authorization=authorization, headers=headers)
            print(f"Realtime MCP prompt: {server.name} {'loaded' if prompt else 'not exposed'}", flush=True)
        except Exception as exc:
            print(f"Realtime MCP prompt: {server.name} unavailable ({exc})", flush=True)
            if strict_probe:
                raise
    return RealtimeMCPServer(
        label=server.name,
        url=server.native.url,
        authorization=authorization,
        headers=headers,
        require_approval="always" if server.realtime.permissions.mode == "approval" else "never",
        context_instructions=prompt,
    )


def _has_local_mcp_route(server: CanonicalMCPServerConfig) -> bool:
    entry = server.local_entry
    command = str(entry.get("command") or "").strip()
    url = str(entry.get("url") or "").strip()
    return bool(command or (url and not url.lower().startswith("https://")))


async def probe_local_stdio(raw_config: dict[str, Any], server: CanonicalMCPServerConfig) -> tuple[bool, str]:
    """Probe one AUTO server through the local bridge without executing a tool."""
    if server.realtime.permissions.mode == "approval":
        return False, "stdio approval is not implemented"
    if not _has_local_mcp_route(server):
        return False, "no local command/private HTTP route configured"

    probe = RealtimeMCPBridge(raw_config, server_names=(server.name,))
    try:
        tools = await probe.start()
        count = len(tools)
        if count < 1:
            return False, "local bridge discovered no tools"
        return True, f"local bridge discovered {count} tool(s)"
    except Exception as exc:
        return False, f"local bridge probe failed: {exc}"
    finally:
        await probe.close()


async def capture_loop(
    engine,
    stream,
    source_rate: int,
    channels: int,
    frames: int,
    stop_event: asyncio.Event,
    capture_filter: Callable[[Any, bytes, asyncio.Event], Awaitable[bool]] | None = None,
) -> None:
    resampler = Pcm16MonoResampler(source_rate, REALTIME_RATE)
    while not stop_event.is_set():
        try:
            pcm = await asyncio.to_thread(stream.read, frames, False)
        except Exception as exc:
            print(f"Realtime input error: {exc}", flush=True)
            stop_event.set()
            return
        realtime_pcm = resampler.process(downmix_pcm16(pcm, channels))
        if realtime_pcm:
            if capture_filter is not None:
                try:
                    consumed = await capture_filter(engine, realtime_pcm, stop_event)
                except Exception as exc:
                    print(f"Realtime capture filter error: {exc}", flush=True)
                    stop_event.set()
                    return
                if consumed:
                    continue
            try:
                await engine.send_audio(realtime_pcm)
            except Exception as exc:
                print(f"Realtime send error: {exc}", flush=True)
                stop_event.set()
                return


async def playback_loop(
    queue: asyncio.Queue,
    stream,
    output_rate: int,
    output_channels: int,
    interrupted: set[str],
    first_played: dict[str, float],
    stop_event: asyncio.Event,
    cloud_gain: float,
    semantic: SemanticAudioController,
    callbacks: RealtimeRuntimeCallbacks | None = None,
) -> None:
    current_response_id = ""
    resampler = Pcm16MonoResampler(REALTIME_RATE, output_rate)
    while not stop_event.is_set():
        response_id, audio = await queue.get()
        if audio is None:
            if response_id not in interrupted:
                transition_semantic(semantic, SemanticAudioState.IDLE, callbacks)
                transition_semantic(semantic, SemanticAudioState.LISTENING, callbacks)
            continue
        if response_id in interrupted:
            continue
        if response_id != current_response_id:
            current_response_id = response_id
            resampler = Pcm16MonoResampler(REALTIME_RATE, output_rate)
        converted = resampler.process(audio)
        if not converted:
            continue
        converted = apply_pcm16_gain(converted, cloud_gain)
        converted = expand_pcm16_channels(converted, output_channels)
        if response_id not in first_played:
            first_played[response_id] = time.perf_counter()
        try:
            await asyncio.to_thread(stream.write, converted)
        except Exception as exc:
            print(f"Realtime output error: {exc}", flush=True)
            stop_event.set()
            return


def clear_queue(queue: asyncio.Queue) -> None:
    while True:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            return


async def execute_bridge_call(
    engine,
    bridge: RealtimeMCPBridge,
    event,
    completed_calls: set[str],
    turn_tracker: RealtimeTurnTracker | None = None,
) -> None:
    call_id = str(event.data.get("call_id") or "")
    name = str(event.data.get("name") or "")
    arguments = str(event.data.get("arguments") or "{}")
    if not call_id or call_id in completed_calls:
        return
    if len(completed_calls) >= 2048:
        completed_calls.clear()
    completed_calls.add(call_id)
    if turn_tracker is not None:
        turn_tracker.tool_started()
    target = bridge.tool_targets.get(name)
    started = time.perf_counter()
    print("Realtime bridge call " + json.dumps({"server": target.server if target else None, "tool": target.tool if target else name, "arguments": arguments}, ensure_ascii=False, separators=(",", ":")), flush=True)
    timeout = max(1.0, float(os.getenv("MCP_AGENT_TIMEOUT_SECONDS", "20") or "20"))
    try:
        result = await asyncio.wait_for(bridge.execute(name, arguments), timeout=timeout)
    except asyncio.TimeoutError:
        result = {"is_error": True, "error": f"MCP call timed out after {timeout:.1f}s; execution state is not replayed automatically"}
    except Exception as exc:
        result = {"is_error": True, "error": str(exc)}
    print(f"Realtime bridge result: call_id={call_id} duration_ms={(time.perf_counter()-started)*1000:.1f}", flush=True)
    delivered = False
    try:
        await engine.submit_tool_result(call_id, result)
        delivered = True
    except Exception as exc:
        print(f"Realtime bridge result delivery failed: call_id={call_id} error={exc}", flush=True)
    finally:
        if turn_tracker is not None:
            turn_tracker.tool_finished(expect_followup=delivered)


async def supervised_text_command_loop(
    engine,
    stop_event: asyncio.Event,
    semantic: SemanticAudioController,
    turn_tracker: RealtimeTurnTracker,
    callbacks: RealtimeRuntimeCallbacks,
) -> None:
    while not stop_event.is_set():
        try:
            if await _pop_cancel_requested(callbacks):
                try:
                    await engine.cancel_response()
                except Exception as exc:
                    print(f"Realtime text cancel warning: {exc}", flush=True)
                turn_tracker.reset_after_cancel_or_failure()
                await _set_busy(callbacks, False)

            command = await _pop_injected_command(callbacks)
            if command:
                text = str(command.get("text") or "").strip()
                if text:
                    print(f"Realtime injected text command: {text}", flush=True)
                    await _append_dialogue(callbacks, "user", text)
                    await _set_busy(callbacks, True)
                    turn_tracker.start_text_turn()
                    transition_semantic(semantic, SemanticAudioState.PROCESSING, callbacks)
                    context_refreshed = await _refresh_engine_context(callbacks, engine)
                    if not context_refreshed:
                        print(
                            "Realtime session context refresh unavailable for this provider; sending clean user text only",
                            flush=True,
                        )
                    await engine.send_text(text)
        except Exception as exc:
            print(f"Realtime supervised text command warning: {exc}", flush=True)
        await asyncio.sleep(0.15)


async def wait_until_ready(engine) -> None:
    while True:
        event = await asyncio.wait_for(engine.next_event(), timeout=25.0)
        if event.type == "ready":
            return
        if event.type in {"provider_error", "connection_error", "connection_closed"}:
            raise RuntimeError(f"Realtime session failed before ready: {event.data}")
        if event.type.startswith("mcp_"):
            print(f"Realtime MCP startup event: {event.type}", flush=True)


async def _announce_phrase(engine, output_stream, output_rate: int, output_channels: int, text: str, cloud_gain: float) -> None:
    instruction = f"Annonce système de démarrage. Prononce exactement cette phrase, sans ajouter un seul mot : {text}"
    print(f"Realtime startup announcement: {text}", flush=True)
    await engine.send_text(instruction)
    resampler = Pcm16MonoResampler(REALTIME_RATE, output_rate)
    while True:
        event = await asyncio.wait_for(engine.next_event(), timeout=30.0)
        if event.type == "audio_delta":
            audio = event.data.get("audio") or b""
            converted = resampler.process(audio)
            if converted:
                converted = apply_pcm16_gain(converted, cloud_gain)
                await asyncio.to_thread(output_stream.write, expand_pcm16_channels(converted, output_channels))
        elif event.type == "transcript_done":
            transcript = str(event.data.get("text") or "").strip()
            if transcript and transcript != text:
                print(f"Realtime startup announcement transcript differed: {transcript}", flush=True)
        elif event.type == "response_done":
            return
        elif event.type in {"provider_error", "connection_error", "connection_closed"}:
            raise RuntimeError(f"Realtime startup announcement failed: {event.data}")
        elif event.type.startswith("mcp_"):
            print(f"Realtime MCP startup event: {event.type}", flush=True)


async def announce_ready(
    engine,
    output_stream,
    output_rate: int,
    output_channels: int,
    connectivity: str,
    cloud_gain: float,
    *,
    tool_count: int,
    has_unknown_native_tools: bool = False,
) -> None:
    stt_language = str(os.getenv("STT_LANGUAGE") or "fr").strip()
    wake_words = get_configured_wake_words()
    connectivity_text = startup_connectivity_message(
        stt_language=stt_language,
        connectivity=connectivity,
    )
    await _announce_phrase(engine, output_stream, output_rate, output_channels, connectivity_text, cloud_gain)
    await asyncio.sleep(0.45)
    ready_text = startup_ready_message(
        stt_language=stt_language,
        tool_count=tool_count,
        has_unknown_native_tools=has_unknown_native_tools,
        wake_words=wake_words,
    )
    await _announce_phrase(engine, output_stream, output_rate, output_channels, ready_text, cloud_gain)


async def event_loop(
    engine,
    bridge: RealtimeMCPBridge | None,
    queue: asyncio.Queue,
    interrupted: set[str],
    first_played: dict[str, float],
    stop_event: asyncio.Event,
    semantic: SemanticAudioController,
    provider_failure: asyncio.Event,
    callbacks: RealtimeRuntimeCallbacks | None = None,
) -> None:
    runtime_callbacks = _callbacks(callbacks)
    completed_calls = _RECENT_BRIDGE_CALL_IDS
    completed_responses: set[str] = set()
    settling_responses: set[str] = set()
    settled_responses: set[str] = set()
    audio_started: set[str] = set()
    tool_tasks: set[asyncio.Task] = set()
    completion_tasks: set[asyncio.Task] = set()
    turn_tracker = RealtimeTurnTracker(
        action_grace_seconds=_float_env("REALTIME_ACTION_GRACE_SECONDS", DEFAULT_REALTIME_ACTION_GRACE_SECONDS)
    )
    last_completed_response: dict[str, Any] = {"id": "", "had_audio": False}
    turn = 0
    command_task: asyncio.Task | None = None

    def schedule_settle(response_id: str, response_had_audio: bool) -> None:
        if not response_id or response_id in settled_responses or response_id in settling_responses:
            return
        settling_responses.add(response_id)

        async def settle_runner() -> None:
            try:
                settled = await settle_completed_response(
                    response_id=response_id,
                    response_had_audio=response_had_audio,
                    turn_tracker=turn_tracker,
                    tool_tasks=tool_tasks,
                    queue=queue,
                    semantic=semantic,
                    callbacks=runtime_callbacks,
                )
                if settled:
                    settled_responses.add(response_id)
            finally:
                settling_responses.discard(response_id)

        task = asyncio.create_task(settle_runner(), name=f"lsa-realtime-settle-{response_id}")
        completion_tasks.add(task)
        task.add_done_callback(completion_tasks.discard)

    def tool_done(_task: asyncio.Task) -> None:
        tool_tasks.discard(_task)
        response_id = str(last_completed_response.get("id") or "")
        if response_id:
            schedule_settle(response_id, bool(last_completed_response.get("had_audio")))

    if runtime_callbacks.has_supervised_text:
        command_task = asyncio.create_task(
            supervised_text_command_loop(engine, stop_event, semantic, turn_tracker, runtime_callbacks),
            name="lsa-realtime-supervised-text",
        )

    try:
        while not stop_event.is_set():
            try:
                event = await asyncio.wait_for(
                    engine.next_event(),
                    timeout=_event_wait_timeout(turn_tracker, tool_tasks),
                )
            except asyncio.TimeoutError:
                turn_timeout = max(
                    0.0,
                    _float_env("REALTIME_TURN_TIMEOUT_SECONDS", DEFAULT_REALTIME_TURN_TIMEOUT_SECONDS),
                )
                if (
                    turn_timeout > 0
                    and turn_tracker.has_pending_work()
                    and not _active_task_exists(tool_tasks)
                    and turn_tracker.age_seconds() >= turn_timeout
                ):
                    await handle_turn_watchdog_timeout(
                        engine=engine,
                        turn_tracker=turn_tracker,
                        interrupted=interrupted,
                        queue=queue,
                        semantic=semantic,
                        callbacks=runtime_callbacks,
                        stop_event=stop_event,
                        provider_failure=provider_failure,
                        timeout_seconds=turn_timeout,
                    )
                    return

                inactivity_timeout = _float_env(
                    "REALTIME_INACTIVITY_TIMEOUT_SECONDS",
                    DEFAULT_REALTIME_INACTIVITY_TIMEOUT_SECONDS,
                )
                if inactivity_timeout > 0 and turn_tracker.age_seconds() >= inactivity_timeout:
                    if turn_tracker.has_ambiguous_action() or _active_task_exists(tool_tasks):
                        print("Realtime inactivity timeout deferred: action still in progress", flush=True)
                        continue
                    print(f"Realtime inactivity timeout after {inactivity_timeout:.1f}s; closing session", flush=True)
                    stop_event.set()
                    return
                continue

            now = time.perf_counter()
            turn_tracker.touch()
            if event.type == "speech_started":
                if _should_ignore_provider_speech_started(runtime_callbacks, semantic.state):
                    print("Realtime speech started ignored while assistant speech is protected by local wake gate", flush=True)
                    continue
                print("Realtime speech started", flush=True)
                turn_tracker.speech_started()
                if turn_tracker.current_response_id:
                    interrupted.add(turn_tracker.current_response_id)
                    clear_queue(queue)
                    try:
                        await engine.cancel_response()
                    except Exception as exc:
                        print(f"Realtime cancellation warning: {exc}", flush=True)
                    turn_tracker.reset_after_cancel_or_failure()
                    await _set_busy(runtime_callbacks, False)
            elif event.type == "speech_stopped":
                turn_tracker.speech_stopped(now)
                print("Realtime speech stopped", flush=True)
            elif event.type == "user_transcript_done":
                text = str(event.data.get("text") or "").strip()
                if text:
                    print(f"Utilisateur: {text}", flush=True)
                    await _append_dialogue(runtime_callbacks, "user", text)
                    await _set_busy(runtime_callbacks, True)
                    turn_tracker.start_text_turn()
            elif event.type == "user_transcript_error":
                print(f"Realtime user transcription error: {_format_user_transcript_error(event.data)}", flush=True)
                if turn_tracker.speech_stopped_at is not None:
                    turn_tracker.start_text_turn()
                    await _set_busy(runtime_callbacks, True)
                    transition_semantic(semantic, SemanticAudioState.PROCESSING, runtime_callbacks)
            elif event.type == "response_started":
                response = event.data.get("response") or {}
                turn_tracker.response_started_event(str(response.get("id") or ""), now)
                await _set_busy(runtime_callbacks, True)
                transition_semantic(semantic, SemanticAudioState.PROCESSING, runtime_callbacks)
            elif event.type == "tool_call" and bridge is not None:
                await _set_busy(runtime_callbacks, True)
                transition_semantic(semantic, SemanticAudioState.PROCESSING, runtime_callbacks)
                task = asyncio.create_task(execute_bridge_call(engine, bridge, event, completed_calls, turn_tracker))
                tool_tasks.add(task)
                task.add_done_callback(tool_done)
            elif event.type == "tool_followup_requested":
                turn_tracker.tool_followup_requested()
                await _set_busy(runtime_callbacks, True)
                transition_semantic(semantic, SemanticAudioState.PROCESSING, runtime_callbacks)
                print("Realtime tool follow-up requested", flush=True)
            elif event.type in {"mcp_list_tools", "mcp_call", "mcp_approval_request", "mcp_event"}:
                print(f"Realtime native MCP event: {event.type}", flush=True)
            elif event.type == "audio_delta":
                response_id = str(event.data.get("response_id") or turn_tracker.current_response_id)
                if response_id in interrupted:
                    continue
                audio = event.data.get("audio") or b""
                if audio:
                    if response_id not in audio_started:
                        audio_started.add(response_id)
                        transition_semantic(semantic, SemanticAudioState.RESULT_READY, runtime_callbacks)
                        transition_semantic(semantic, SemanticAudioState.SPEAKING, runtime_callbacks)
                    turn_tracker.audio_started(response_id, now)
                    await queue.put((response_id, audio))
            elif event.type == "transcript_done":
                text = str(event.data.get("text") or "").strip()
                if text:
                    print(f"Assistant: {text}", flush=True)
                    await _append_dialogue(runtime_callbacks, "assistant", text)
            elif event.type == "response_done":
                response_id = str(event.data.get("response_id") or turn_tracker.current_response_id)
                if response_id in completed_responses:
                    continue
                completed_responses.add(response_id)
                turn += 1
                metric_values = turn_tracker.metrics_for(response_id, now)
                speech_end = turn_tracker.speech_stop_by_response.get(response_id)
                metrics = {
                    "pipeline": "realtime",
                    "provider": engine.config.provider,
                    "model": engine.config.model,
                    "turn": turn,
                    "response_id": response_id,
                    "speech_end_to_first_audio_ms": metric_values["speech_end_to_first_audio_ms"],
                    "speech_end_to_first_playback_ms": round((first_played[response_id] - speech_end) * 1000, 1)
                    if speech_end is not None and response_id in first_played
                    else None,
                    "response_start_to_done_ms": metric_values["response_start_to_done_ms"],
                    "usage": event.data.get("usage") or {},
                }
                metrics["cost_usd"] = (
                    realtime_usage_cost_usd(engine.config.model, metrics["usage"])
                    if engine.config.provider == "openai"
                    else None
                )
                print("REALTIME_METRICS " + json.dumps(metrics, ensure_ascii=False, separators=(",", ":")), flush=True)
                turn_tracker.response_done(response_id)
                last_completed_response["id"] = response_id
                last_completed_response["had_audio"] = response_id in audio_started
                schedule_settle(response_id, response_id in audio_started)
            elif event.type in {"provider_error", "connection_error"}:
                if event.type == "provider_error" and is_benign_provider_error(event.data):
                    print(f"Realtime provider benign warning: {event.data}", flush=True)
                    continue
                print(f"Realtime provider error: {event.data}", flush=True)
                if turn_tracker.has_ambiguous_action():
                    print(
                        "Realtime action state reset after provider error; no in-flight action will be replayed automatically",
                        flush=True,
                    )
                turn_tracker.reset_after_cancel_or_failure()
                await _set_busy(runtime_callbacks, False)
                provider_failure.set()
                stop_event.set()
            elif event.type == "connection_closed":
                print("Realtime connection closed", flush=True)
                if turn_tracker.has_ambiguous_action():
                    print(
                        "Realtime action state reset after connection close; no in-flight action will be replayed automatically",
                        flush=True,
                    )
                turn_tracker.reset_after_cancel_or_failure()
                await _set_busy(runtime_callbacks, False)
                provider_failure.set()
                stop_event.set()
    finally:
        if command_task is not None:
            command_task.cancel()
        for task in tuple(tool_tasks):
            task.cancel()
        for task in tuple(completion_tasks):
            task.cancel()
        pending = [task for task in (command_task, *tool_tasks, *completion_tasks) if task is not None]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


async def run(args, runtime_callbacks: RealtimeRuntimeCallbacks | None = None) -> int:
    env_file = Path(args.env_file).resolve()
    load_dotenv(env_file, override=True)
    connectivity = str(os.getenv("CONNECTIVITY_MODE") or "online").strip().lower()
    runtime_callbacks = _callbacks(runtime_callbacks)
    play_startup_sound(env_file)

    provider = str(os.getenv("LSA_REALTIME_PROVIDER") or "openai").strip().lower()
    if provider == "gemini":
        api_key = read_secret("GEMINI_API_KEY", env_file)
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY / GEMINI_API_KEY_FILE is not configured")
        model = str(os.getenv("GEMINI_LIVE_MODEL") or DEFAULT_GEMINI_MODEL).strip()
        voice = str(os.getenv("GEMINI_LIVE_VOICE") or DEFAULT_GEMINI_VOICE).strip()
    elif provider == "openai":
        api_key = read_secret("OPENAI_API_KEY", env_file)
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY / OPENAI_API_KEY_FILE is not configured")
        model = str(os.getenv("OPENAI_REALTIME_MODEL") or DEFAULT_MODEL).strip()
        voice = str(os.getenv("OPENAI_REALTIME_VOICE") or DEFAULT_VOICE).strip()
    else:
        raise RuntimeError(f"unsupported realtime provider: {provider!r}")
    output_gains = VoiceOutputGains.from_env()
    semantic_config = SemanticAudioConfig.from_env()
    config_path = resolve_path(str(os.getenv("MCP_CONFIG") or "mcp_servers.json").strip(), env_file)
    raw_config = json.loads(config_path.read_text(encoding="utf-8"))
    raw_servers = raw_config.get("mcpServers") if isinstance(raw_config, dict) else None
    if isinstance(raw_servers, dict):
        raw_config = dict(raw_config)
        raw_config["mcpServers"] = {name: entry for name, entry in raw_servers.items() if not isinstance(entry, dict) or entry.get("enabled", True) is not False}
    inventory = {name: server for name, server in load_mcp_inventory(config_path).items() if server.raw_entry.get("enabled", True) is not False}

    native_servers: list[RealtimeMCPServer] = []
    bridge_names: list[str] = []
    for server in inventory.values():
        transport = server.realtime.transport
        if provider == "gemini":
            if server.realtime.permissions.mode == "approval":
                raise RuntimeError(f"Gemini bridge approval is not implemented for MCP {server.name!r}; use Open or disable the server")
            if transport == "native":
                raise RuntimeError(f"Gemini Live has no provider-native MCP adapter for {server.name!r}; select Auto or STDIO")
            if transport == "auto":
                local_ok, local_reason = await probe_local_stdio(raw_config, server)
                if not local_ok:
                    raise RuntimeError(f"AUTO MCP {server.name!r} has no healthy local bridge for Gemini: {local_reason}")
                print(f"Realtime MCP auto selection: {server.name} -> stdio ({local_reason})", flush=True)
            bridge_names.append(server.name)
            continue
        if transport == "auto":
            print(f"Realtime MCP auto selection: {server.name} -> probing local STDIO/bridge first", flush=True)
            local_ok, local_reason = await probe_local_stdio(raw_config, server)
            if local_ok:
                bridge_names.append(server.name)
                print(f"Realtime MCP auto selection: {server.name} -> stdio ({local_reason})", flush=True)
                continue

            print(f"Realtime MCP auto local unavailable: {server.name} ({local_reason})", flush=True)
            if server.native.url:
                print(f"Realtime MCP auto selection: {server.name} -> probing native", flush=True)
                try:
                    native_servers.append(await native_server(server, strict_probe=True))
                    print(f"Realtime MCP auto selection: {server.name} -> native", flush=True)
                    continue
                except Exception as exc:
                    decision = classify_auto_fallback(dispatched=False, read_only=None)
                    print("Realtime MCP auto native unavailable " + json.dumps({"server":server.name,"fallback":decision.fallback,"classification":decision.classification,"reason":decision.reason,"native_error":str(exc)}, ensure_ascii=False, separators=(",", ":")), flush=True)
            raise RuntimeError(f"AUTO MCP {server.name!r} has no healthy local route and no healthy native route")

        if transport == "native":
            native_servers.append(await native_server(server))
        elif transport == "stdio":
            if server.realtime.permissions.mode == "approval":
                raise RuntimeError(f"STDIO approval is not implemented yet for MCP {server.name!r}")
            bridge_names.append(server.name)

    bridge: RealtimeMCPBridge | None = None
    function_tools = ()
    loaded_mcp_prompts: list[tuple[str, str]] = []
    for native in native_servers:
        prompt = str(getattr(native, "context_instructions", "") or "").strip()
        if prompt:
            loaded_mcp_prompts.append((str(getattr(native, "label", "native") or "native"), prompt))
    if bridge_names:
        bridge = RealtimeMCPBridge(raw_config, server_names=tuple(bridge_names))
        function_tools = await bridge.start()
        print(f"Realtime MCP stdio tools loaded: {len(function_tools)}", flush=True)
        for server_name in bridge_names:
            prompt = str(await bridge.load_prompt_text(server_name) or "").strip()
            if prompt:
                loaded_mcp_prompts.append((server_name, prompt))
    _log_loaded_mcp_prompts([*bridge_names, *(native.label for native in native_servers)], loaded_mcp_prompts)
    mcp_prompt = _format_loaded_mcp_prompts(loaded_mcp_prompts)
    effective_instructions = compose_realtime_instructions(mcp_prompt=mcp_prompt)
    available_tool_count = len(function_tools) + sum(len(server.allowed_tools or ()) for server in native_servers)
    has_unknown_native_tools = any(not server.allowed_tools for server in native_servers)

    pa = pyaudio.PyAudio()
    input_stream = output_stream = None
    engine = None
    cue_player: SemanticCuePlayer | None = None
    semantic: SemanticAudioController | None = None
    tasks: list[asyncio.Task] = []
    stop_event = asyncio.Event()
    queue: asyncio.Queue = asyncio.Queue()
    interrupted: set[str] = set()
    first_played: dict[str, float] = {}
    provider_failure = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    try:
        input_selected = str(os.getenv("BACKEND_AUDIO_INPUT_DEVICE") or "")
        output_selected = str(os.getenv("BACKEND_AUDIO_OUTPUT_DEVICE") or "")
        input_stream, input_rate, input_channels, input_frames, input_name = open_configured_input(pa, input_selected)
        output_stream, output_rate, output_channels, output_name = open_configured_output(pa, output_selected)
        print(f"Realtime input: {input_name} {input_channels}ch/{input_rate}Hz -> 24kHz mono", flush=True)
        print(f"Realtime output: 24kHz mono -> {output_name} {output_channels}ch/{output_rate}Hz cloud_gain={output_gains.cloud:.2f}", flush=True)
        print(f"Realtime MCP native: {[item.label for item in native_servers] or 'none'}", flush=True)
        print(f"Realtime MCP stdio bridge: {bridge_names or 'none'}", flush=True)

        cue_player = SemanticCuePlayer()
        semantic = SemanticAudioController(
            semantic_config,
            play_once=cue_player.play_once,
            start_loop=cue_player.start_loop,
            stop_loop=cue_player.stop_loop,
            on_state=lambda state: print(f"LSA semantic state: {state.value}", flush=True),
        )

        engine = create_realtime_engine(provider, RealtimeEngineConfig(provider=provider, model=model, voice=voice, instructions=effective_instructions, output_speed=max(0.6, min(1.8, _float_env("WEB_TTS_SPEED", 1.0))), server_vad=True, mcp_servers=tuple(native_servers), function_tools=tuple(function_tools)), api_key=api_key)
        await engine.start()
        await wait_until_ready(engine)
        print(f"LSA Realtime ready: provider={provider} model={model} voice={voice}", flush=True)
        await announce_ready(
            engine,
            output_stream,
            output_rate,
            output_channels,
            connectivity,
            output_gains.cloud,
            tool_count=available_tool_count,
            has_unknown_native_tools=has_unknown_native_tools,
        )
        transition_semantic(semantic, SemanticAudioState.READY, runtime_callbacks)
        transition_semantic(semantic, SemanticAudioState.LISTENING, runtime_callbacks)
        print("LSA Realtime listening: provider VAD active", flush=True)

        tasks = [
            asyncio.create_task(
                capture_loop(
                    engine,
                    input_stream,
                    input_rate,
                    input_channels,
                    input_frames,
                    stop_event,
                    runtime_callbacks.capture_filter,
                ),
                name="lsa-realtime-capture",
            ),
            asyncio.create_task(
                event_loop(
                    engine,
                    bridge,
                    queue,
                    interrupted,
                    first_played,
                    stop_event,
                    semantic,
                    provider_failure,
                    runtime_callbacks,
                ),
                name="lsa-realtime-events",
            ),
            asyncio.create_task(
                playback_loop(
                    queue,
                    output_stream,
                    output_rate,
                    output_channels,
                    interrupted,
                    first_played,
                    stop_event,
                    output_gains.cloud,
                    semantic,
                    runtime_callbacks,
                ),
                name="lsa-realtime-playback",
            ),
        ]
        await stop_event.wait()
        return TRANSIENT_PROVIDER_EXIT if provider_failure.is_set() else 0
    finally:
        stop_event.set()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if semantic is not None:
            semantic.close()
        if cue_player is not None:
            cue_player.close()
        if engine is not None:
            await engine.stop()
        if bridge is not None:
            await bridge.close()
        for stream in (input_stream, output_stream):
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
        pa.terminate()


def main(
    runtime_callbacks_factory: Callable[[str | Path], RealtimeRuntimeCallbacks | None] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True)
    args = parser.parse_args()
    attempts = max(0, int(os.getenv("REALTIME_RECONNECT_ATTEMPTS", "3") or "3"))
    backoff = max(0.2, float(os.getenv("REALTIME_RECONNECT_BACKOFF_SECONDS", "1.0") or "1.0"))
    for attempt in range(attempts + 1):
        try:
            load_dotenv(Path(args.env_file).resolve(), override=True)
            runtime_callbacks = runtime_callbacks_factory(args.env_file) if runtime_callbacks_factory is not None else None
            code = asyncio.run(run(args, runtime_callbacks=runtime_callbacks))
        except KeyboardInterrupt:
            return 130
        except Exception as exc:
            print(f"LSA Realtime failed: {exc}", file=sys.stderr, flush=True)
            code = TRANSIENT_PROVIDER_EXIT
        if code != TRANSIENT_PROVIDER_EXIT:
            return code
        if attempt >= attempts:
            print("LSA Realtime reconnect budget exhausted", file=sys.stderr, flush=True)
            return 1
        delay = min(8.0, backoff * (2 ** attempt))
        print(f"LSA Realtime reconnect: attempt={attempt + 1}/{attempts} delay={delay:.1f}s", flush=True)
        time.sleep(delay)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
