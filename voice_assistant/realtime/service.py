#!/usr/bin/env python3
"""Integrated provider-neutral Realtime runtime for LiveStageAssistant."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from typing import Any

import pyaudio
from dotenv import load_dotenv

from ..semantic_audio import (
    SemanticAudioConfig,
    SemanticAudioController,
    SemanticAudioState,
    VoiceOutputGains,
)
from ..semantic_audio_output import SemanticCuePlayer
from .audio import Pcm16MonoResampler, apply_pcm16_gain, downmix_pcm16, expand_pcm16_channels
from .audio_devices import PipeWireInputStream, PipeWireOutputStream, parse_pipewire_selector
from .engine import RealtimeEngineConfig, RealtimeMCPServer
from .mcp_auto import classify_auto_fallback
from .mcp_bridge import RealtimeMCPBridge, load_remote_mcp_prompt
from .mcp_config import CanonicalMCPServerConfig, load_mcp_inventory
from .metrics import realtime_usage_cost_usd
from .provider_factory import create_realtime_engine
from .prompts import DEFAULT_BASE_PROMPT

ROOT = Path(__file__).resolve().parents[2]
REALTIME_RATE = 24000
DEFAULT_MODEL = "gpt-realtime-2.1"
DEFAULT_VOICE = "marin"
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-live-preview"
DEFAULT_GEMINI_VOICE = "Kore"
TRANSIENT_PROVIDER_EXIT = 75
_RECENT_BRIDGE_CALL_IDS: set[str] = set()



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
    if str(os.getenv("MCP_LOAD_SERVER_PROMPT", "true")).strip().lower() not in {"0", "false", "no", "off"}:
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


async def capture_loop(engine, stream, source_rate: int, channels: int, frames: int, stop_event: asyncio.Event) -> None:
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
) -> None:
    current_response_id = ""
    resampler = Pcm16MonoResampler(REALTIME_RATE, output_rate)
    while not stop_event.is_set():
        response_id, audio = await queue.get()
        if audio is None:
            if response_id not in interrupted:
                semantic.transition(SemanticAudioState.IDLE)
                semantic.transition(SemanticAudioState.LISTENING)
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


async def execute_bridge_call(engine, bridge: RealtimeMCPBridge, event, completed_calls: set[str]) -> None:
    call_id = str(event.data.get("call_id") or "")
    name = str(event.data.get("name") or "")
    arguments = str(event.data.get("arguments") or "{}")
    if not call_id or call_id in completed_calls:
        return
    if len(completed_calls) >= 2048:
        completed_calls.clear()
    completed_calls.add(call_id)
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
    try:
        await engine.submit_tool_result(call_id, result)
    except Exception as exc:
        # The MCP call may already have changed external state. Never replay it
        # merely because the provider connection disappeared before receiving
        # the tool result.
        print(f"Realtime bridge result delivery failed: call_id={call_id} error={exc}", flush=True)


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
    instruction = f"Annonce système de démarrage. Prononce exactement cette phrase en français, sans ajouter un seul mot : {text}"
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
            print(f"Realtime startup announcement transcript: {str(event.data.get('text') or '').strip()}", flush=True)
        elif event.type == "response_done":
            return
        elif event.type in {"provider_error", "connection_error", "connection_closed"}:
            raise RuntimeError(f"Realtime startup announcement failed: {event.data}")
        elif event.type.startswith("mcp_"):
            print(f"Realtime MCP startup event: {event.type}", flush=True)


async def announce_ready(engine, output_stream, output_rate: int, output_channels: int, connectivity: str, cloud_gain: float) -> None:
    connectivity_text = "Assistant connecté à internet." if connectivity == "online" else "Assistant hors ligne."
    await _announce_phrase(engine, output_stream, output_rate, output_channels, connectivity_text, cloud_gain)
    await asyncio.sleep(0.45)
    await _announce_phrase(engine, output_stream, output_rate, output_channels, "Assistant vocal prêt à exécuter des commandes.", cloud_gain)


async def event_loop(
    engine,
    bridge: RealtimeMCPBridge | None,
    queue: asyncio.Queue,
    interrupted: set[str],
    first_played: dict[str, float],
    stop_event: asyncio.Event,
    semantic: SemanticAudioController,
    provider_failure: asyncio.Event,
) -> None:
    current_response_id = ""
    completed_calls = _RECENT_BRIDGE_CALL_IDS
    completed_responses: set[str] = set()
    audio_started: set[str] = set()
    tool_tasks: set[asyncio.Task] = set()
    speech_stopped_at: float | None = None
    response_started: dict[str, float] = {}
    speech_stop_by_response: dict[str, float] = {}
    first_audio_received: dict[str, float] = {}
    turn = 0
    while not stop_event.is_set():
        event = await engine.next_event()
        now = time.perf_counter()
        if event.type == "speech_started":
            print("Realtime speech started", flush=True)
            if current_response_id:
                interrupted.add(current_response_id)
                clear_queue(queue)
                try:
                    await engine.cancel_response()
                except Exception as exc:
                    print(f"Realtime cancellation warning: {exc}", flush=True)
        elif event.type == "speech_stopped":
            speech_stopped_at = now
            print("Realtime speech stopped", flush=True)
        elif event.type == "user_transcript_done":
            text = str(event.data.get("text") or "").strip()
            if text:
                print(f"Utilisateur: {text}", flush=True)
        elif event.type == "response_started":
            response = event.data.get("response") or {}
            current_response_id = str(response.get("id") or "")
            response_started[current_response_id] = now
            if speech_stopped_at is not None:
                speech_stop_by_response[current_response_id] = speech_stopped_at
            semantic.transition(SemanticAudioState.PROCESSING)
        elif event.type == "tool_call" and bridge is not None:
            task = asyncio.create_task(execute_bridge_call(engine, bridge, event, completed_calls))
            tool_tasks.add(task)
            task.add_done_callback(tool_tasks.discard)
        elif event.type in {"mcp_list_tools", "mcp_call", "mcp_approval_request", "mcp_event", "mcp_followup_requested"}:
            print(f"Realtime native MCP event: {event.type}", flush=True)
        elif event.type == "audio_delta":
            response_id = str(event.data.get("response_id") or current_response_id)
            if response_id in interrupted:
                continue
            audio = event.data.get("audio") or b""
            if audio:
                if response_id not in audio_started:
                    audio_started.add(response_id)
                    semantic.transition(SemanticAudioState.RESULT_READY)
                    semantic.transition(SemanticAudioState.SPEAKING)
                first_audio_received.setdefault(response_id, now)
                await queue.put((response_id, audio))
        elif event.type == "transcript_done":
            text = str(event.data.get("text") or "").strip()
            if text:
                print(f"Assistant: {text}", flush=True)
        elif event.type == "response_done":
            response_id = str(event.data.get("response_id") or current_response_id)
            if response_id in completed_responses:
                continue
            completed_responses.add(response_id)
            turn += 1
            speech_end = speech_stop_by_response.get(response_id)
            metrics = {"pipeline":"realtime","provider":engine.config.provider,"model":engine.config.model,"turn":turn,"response_id":response_id,"speech_end_to_first_audio_ms":round((first_audio_received[response_id]-speech_end)*1000,1) if speech_end is not None and response_id in first_audio_received else None,"speech_end_to_first_playback_ms":round((first_played[response_id]-speech_end)*1000,1) if speech_end is not None and response_id in first_played else None,"response_start_to_done_ms":round((now-response_started[response_id])*1000,1) if response_id in response_started else None,"usage":event.data.get("usage") or {}}
            metrics["cost_usd"] = realtime_usage_cost_usd(engine.config.model, metrics["usage"]) if engine.config.provider == "openai" else None
            print("REALTIME_METRICS " + json.dumps(metrics, ensure_ascii=False, separators=(",", ":")), flush=True)
            if response_id in audio_started:
                await queue.put((response_id, None))
            if current_response_id == response_id:
                current_response_id = ""
        elif event.type in {"provider_error", "connection_error"}:
            print(f"Realtime provider error: {event.data}", flush=True)
            provider_failure.set()
        elif event.type == "connection_closed":
            print("Realtime connection closed", flush=True)
            provider_failure.set()
            stop_event.set()
    for task in tuple(tool_tasks):
        task.cancel()
    if tool_tasks:
        await asyncio.gather(*tool_tasks, return_exceptions=True)


async def run(args) -> int:
    env_file = Path(args.env_file).resolve()
    load_dotenv(env_file, override=True)
    connectivity = str(os.getenv("CONNECTIVITY_MODE") or "online").strip().lower()
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
    if bridge_names:
        bridge = RealtimeMCPBridge(raw_config, server_names=tuple(bridge_names))
        function_tools = await bridge.start()
        print(f"Realtime MCP stdio tools loaded: {len(function_tools)}", flush=True)

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

        engine = create_realtime_engine(provider, RealtimeEngineConfig(provider=provider, model=model, voice=voice, instructions=DEFAULT_BASE_PROMPT, server_vad=True, mcp_servers=tuple(native_servers), function_tools=tuple(function_tools)), api_key=api_key)
        await engine.start()
        await wait_until_ready(engine)
        print(f"LSA Realtime ready: provider={provider} model={model} voice={voice}", flush=True)
        await announce_ready(engine, output_stream, output_rate, output_channels, connectivity, output_gains.cloud)
        semantic.transition(SemanticAudioState.READY)
        semantic.transition(SemanticAudioState.LISTENING)
        print("LSA Realtime listening: provider VAD active", flush=True)

        tasks = [
            asyncio.create_task(capture_loop(engine, input_stream, input_rate, input_channels, input_frames, stop_event), name="lsa-realtime-capture"),
            asyncio.create_task(event_loop(engine, bridge, queue, interrupted, first_played, stop_event, semantic, provider_failure), name="lsa-realtime-events"),
            asyncio.create_task(playback_loop(queue, output_stream, output_rate, output_channels, interrupted, first_played, stop_event, output_gains.cloud, semantic), name="lsa-realtime-playback"),
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True)
    args = parser.parse_args()
    attempts = max(0, int(os.getenv("REALTIME_RECONNECT_ATTEMPTS", "3") or "3"))
    backoff = max(0.2, float(os.getenv("REALTIME_RECONNECT_BACKOFF_SECONDS", "1.0") or "1.0"))
    for attempt in range(attempts + 1):
        try:
            code = asyncio.run(run(args))
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
