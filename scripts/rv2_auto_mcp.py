#!/usr/bin/env python3
"""RV2C native-first Realtime MCP auto/fallback validation runner.

Auto semantics are deliberately conservative:
- start with provider-native remote MCP only;
- do not start the LSA bridge while native is healthy;
- on a clear pre-dispatch native failure, switch to the existing LSA bridge;
- after native dispatch, replay through the bridge only for read-only tools or
  when future provider signals explicitly prove non-execution;
- never replay an ambiguous mutation automatically.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import signal
import sys
import time
from typing import Any

import pyaudio
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import rv1_realtime_audio as rv1
from scripts import rv2_native_mcp as native_runner
from scripts import rv2_stdio_mcp as stdio_runner
from voice_assistant.realtime.engine import RealtimeEngineConfig
from voice_assistant.realtime.mcp_auto import classify_auto_fallback, tool_read_only_from_metadata
from voice_assistant.realtime.mcp_bridge import RealtimeMCPBridge
from voice_assistant.realtime.metrics import realtime_usage_cost_usd
from voice_assistant.realtime.openai_realtime import OpenAIRealtimeEngine


RV2_METRICS_PREFIX = "RV2_METRICS "
DEFAULT_INSTRUCTIONS = """You are Live Stage Assistant in RV2C auto MCP validation.
Follow the language of the user's latest utterance. If unclear, default to English.
Keep spoken replies concise unless the user explicitly asks for detail.
Use the tools whenever needed to inspect or control the connected system.
Never invent a tool result and never claim an external action succeeded unless a tool result confirms it.
When a tool is needed, do not speak a waiting/filler sentence first. Call the tool, wait for its result, then answer once with the verified result.
When interrupted, abandon the previous spoken response and handle only the new utterance.
If the user only asks you to stop speaking or be silent, stop without a spoken acknowledgement.
"""


@dataclass
class AutoState:
    active_transport: str = "native"
    last_user_text: str = ""
    native_tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    active_native_call: dict[str, Any] | None = None
    fallback_reason: str = ""
    fallback_classification: str = ""
    replay_text: str = ""


def native_tool_index_from_item(item: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for tool in item.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        if name:
            index[name] = tool
    return index


def log_auto_decision(decision, *, trigger: str) -> None:
    print(
        "RV2 AUTO decision "
        + json.dumps(
            {
                "trigger": trigger,
                "fallback": decision.fallback,
                "classification": decision.classification,
                "reason": decision.reason,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        flush=True,
    )


def request_fallback(state: AutoState, switch_event: asyncio.Event, decision, *, replay: bool) -> None:
    state.fallback_reason = decision.reason
    state.fallback_classification = decision.classification
    state.replay_text = state.last_user_text if replay else ""
    switch_event.set()


async def wait_native_ready_and_discovery(
    engine: OpenAIRealtimeEngine,
    state: AutoState,
    *,
    timeout: float = 15.0,
) -> tuple[bool, str]:
    """Return (ready, discovery_status) before live capture starts."""
    ready = False
    discovery_status = "pending"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = max(0.1, deadline - time.monotonic())
        try:
            event = await asyncio.wait_for(engine.next_event(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        if event.type == "ready":
            ready = True
            continue
        if event.type == "mcp_list_tools":
            item = event.data.get("item") or {}
            native_runner.print_mcp_event(event, {})
            if event.data.get("phase") == "done":
                state.native_tools.update(native_tool_index_from_item(item))
                if item.get("error"):
                    return ready, "failed"
                discovery_status = "completed"
                if ready:
                    return ready, discovery_status
            continue
        if event.type == "mcp_event":
            event_type = str(event.data.get("event_type") or "")
            raw = event.data.get("event") or {}
            print("RV2 native MCP event " + json.dumps(raw, ensure_ascii=False, separators=(",", ":")), flush=True)
            if "mcp_list_tools" in event_type and event_type.endswith(".failed"):
                return ready, "failed"
            if "mcp_list_tools" in event_type and event_type.endswith(".completed"):
                discovery_status = "completed"
                if ready:
                    return ready, discovery_status
            continue
        if event.type in {"provider_error", "connection_error", "connection_closed"}:
            print(f"RV2 native startup failure: {event.type} {event.data}", flush=True)
            return ready, "failed"
    return ready, discovery_status


async def execute_bridge_call(engine, bridge, event, completed_calls: set[str]) -> None:
    call_id = str(event.data.get("call_id") or "")
    name = str(event.data.get("name") or "")
    arguments = str(event.data.get("arguments") or "{}")
    if not call_id or call_id in completed_calls:
        return
    completed_calls.add(call_id)
    target = bridge.tool_targets.get(name)
    started_at = time.perf_counter()
    print(
        "RV2 AUTO bridge call "
        + json.dumps(
            {
                "phase": "started",
                "transport": "stdio/bridge",
                "call_id": call_id,
                "function": name,
                "server": target.server if target else None,
                "tool": target.tool if target else None,
                "arguments": arguments,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        flush=True,
    )
    try:
        result = await bridge.execute(name, arguments)
    except Exception as exc:
        result = {
            "transport": "stdio/bridge",
            "server": target.server if target else None,
            "tool": target.tool if target else name,
            "is_error": True,
            "error": str(exc),
        }
    duration_ms = round((time.perf_counter() - started_at) * 1000.0, 3)
    print(
        "RV2 AUTO bridge call "
        + json.dumps(
            {"phase": "done", "call_id": call_id, "duration_ms": duration_ms, **stdio_runner.bridge_result_summary(result)},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        flush=True,
    )
    await engine.submit_tool_result(call_id, result)
    print(f"RV2 AUTO bridge result submitted: call_id={call_id}", flush=True)


async def event_loop(
    engine,
    state: AutoState,
    mode: str,
    bridge,
    playback_queue,
    interrupted_responses,
    first_audio_played,
    session_stop,
    global_stop,
    switch_event,
) -> None:
    current_response_id = ""
    speech_stopped_at: float | None = None
    response_started_at: dict[str, float] = {}
    speech_stop_by_response: dict[str, float] = {}
    first_audio_received: dict[str, float] = {}
    completed_response_ids: set[str] = set()
    completed_calls: set[str] = set()
    tool_tasks: set[asyncio.Task] = set()
    call_started: dict[str, float] = {}
    turn_index = 0

    while not session_stop.is_set() and not global_stop.is_set():
        event = await engine.next_event()
        now = time.perf_counter()
        if event.type == "speech_started":
            print("RV2 speech started", flush=True)
            if current_response_id:
                interrupted_responses.add(current_response_id)
                rv1.clear_playback_queue(playback_queue)
        elif event.type == "speech_stopped":
            speech_stopped_at = now
            print("RV2 speech stopped", flush=True)
        elif event.type == "user_transcript_done":
            text = str(event.data.get("text") or "").strip()
            if text:
                state.last_user_text = text
                print(f"Utilisateur: {text}", flush=True)
        elif event.type == "user_transcript_error":
            print("RV2 user transcription error: " + json.dumps(event.data, ensure_ascii=False), flush=True)
        elif event.type == "response_started":
            response = event.data.get("response") or {}
            current_response_id = str(response.get("id") or "")
            response_started_at[current_response_id] = now
            if speech_stopped_at is not None:
                speech_stop_by_response[current_response_id] = speech_stopped_at
        elif mode == "native" and event.type == "mcp_list_tools":
            native_runner.print_mcp_event(event, call_started)
            item = event.data.get("item") or {}
            if event.data.get("phase") == "done":
                state.native_tools.update(native_tool_index_from_item(item))
                if item.get("error") and state.active_native_call is None:
                    decision = classify_auto_fallback(dispatched=False, read_only=None)
                    log_auto_decision(decision, trigger="native_list_tools_error")
                    request_fallback(state, switch_event, decision, replay=False)
                    session_stop.set()
        elif mode == "native" and event.type == "mcp_call":
            native_runner.print_mcp_event(event, call_started)
            item = event.data.get("item") or {}
            phase = event.data.get("phase")
            name = str(item.get("name") or "")
            if phase == "added":
                metadata = state.native_tools.get(name)
                state.active_native_call = {
                    "name": name,
                    "arguments": item.get("arguments") or "{}",
                    "read_only": tool_read_only_from_metadata(metadata),
                }
            elif phase == "done":
                active = state.active_native_call or {}
                read_only = active.get("read_only")
                error = item.get("error")
                state.active_native_call = None
                if error:
                    decision = classify_auto_fallback(dispatched=True, read_only=read_only)
                    log_auto_decision(decision, trigger="native_call_error")
                    if decision.fallback:
                        request_fallback(state, switch_event, decision, replay=True)
                        session_stop.set()
        elif mode == "native" and event.type == "mcp_event":
            raw = event.data.get("event") or {}
            event_type = str(event.data.get("event_type") or "")
            print("RV2 native MCP event " + json.dumps(raw, ensure_ascii=False, separators=(",", ":")), flush=True)
            if event_type.endswith(".failed") and state.active_native_call is None:
                decision = classify_auto_fallback(dispatched=False, read_only=None)
                log_auto_decision(decision, trigger=event_type)
                request_fallback(state, switch_event, decision, replay=False)
                session_stop.set()
        elif mode == "stdio" and event.type == "tool_call":
            task = asyncio.create_task(
                execute_bridge_call(engine, bridge, event, completed_calls),
                name=f"rv2c-tool-{event.data.get('call_id') or 'unknown'}",
            )
            tool_tasks.add(task)
            task.add_done_callback(tool_tasks.discard)
        elif event.type == "audio_delta":
            response_id = str(event.data.get("response_id") or current_response_id)
            if response_id in interrupted_responses:
                continue
            audio = event.data.get("audio") or b""
            if not audio:
                continue
            if response_id not in first_audio_received:
                first_audio_received[response_id] = now
            await playback_queue.put((response_id, audio))
        elif event.type == "transcript_done":
            text = str(event.data.get("text") or "").strip()
            if text:
                print(f"Assistant: {text}", flush=True)
        elif event.type == "response_done":
            response_id = str(event.data.get("response_id") or current_response_id)
            if response_id and response_id in completed_response_ids:
                continue
            if response_id:
                completed_response_ids.add(response_id)
            turn_index += 1
            usage = event.data.get("usage") or {}
            speech_end = speech_stop_by_response.get(response_id)
            first_received = first_audio_received.get(response_id)
            first_played = first_audio_played.get(response_id)
            metrics = {
                "schema": 1,
                "pipeline": "realtime",
                "provider": "openai",
                "model": engine.config.model,
                "mcp_transport": "native" if mode == "native" else "stdio/bridge",
                "auto_mode": True,
                "turn": turn_index,
                "response_id": response_id,
                "status": event.data.get("status"),
                "interrupted": response_id in interrupted_responses,
                "speech_end_to_first_audio_ms": round((first_received - speech_end) * 1000.0, 3) if speech_end is not None and first_received is not None else None,
                "speech_end_to_first_playback_ms": round((first_played - speech_end) * 1000.0, 3) if speech_end is not None and first_played is not None else None,
                "speech_end_to_response_done_ms": round((now - speech_end) * 1000.0, 3) if speech_end is not None else None,
                "response_start_to_done_ms": round((now - response_started_at[response_id]) * 1000.0, 3) if response_id in response_started_at else None,
                "usage": usage,
                "cost_usd": realtime_usage_cost_usd(engine.config.model, usage),
            }
            print(RV2_METRICS_PREFIX + json.dumps(metrics, separators=(",", ":"), ensure_ascii=False), flush=True)
            if current_response_id == response_id:
                current_response_id = ""
        elif event.type in {"provider_error", "connection_error", "connection_closed"}:
            print(f"RV2 {mode} provider event: {event.type} {event.data}", flush=True)
            if mode == "native":
                active = state.active_native_call
                decision = classify_auto_fallback(
                    dispatched=active is not None,
                    read_only=active.get("read_only") if active else None,
                )
                log_auto_decision(decision, trigger=event.type)
                if decision.fallback:
                    request_fallback(state, switch_event, decision, replay=active is not None)
                    session_stop.set()
                else:
                    print("RV2 AUTO fallback suppressed: native mutation outcome is ambiguous.", flush=True)
                    global_stop.set()
                    session_stop.set()
            else:
                global_stop.set()
                session_stop.set()

    for task in tuple(tool_tasks):
        task.cancel()
    if tool_tasks:
        await asyncio.gather(*tool_tasks, return_exceptions=True)


async def run_transport_session(
    engine,
    state,
    mode,
    bridge,
    input_stream,
    input_rate,
    input_channels,
    input_frames,
    output_stream,
    output_rate,
    output_channels,
    playback_queue,
    interrupted_responses,
    first_audio_played,
    global_stop,
    switch_event,
) -> str:
    session_stop = asyncio.Event()
    tasks = [
        asyncio.create_task(rv1.capture_loop(engine, input_stream, input_rate, input_channels, input_frames, session_stop), name=f"rv2c-{mode}-capture"),
        asyncio.create_task(event_loop(engine, state, mode, bridge, playback_queue, interrupted_responses, first_audio_played, session_stop, global_stop, switch_event), name=f"rv2c-{mode}-events"),
        asyncio.create_task(rv1.playback_loop(playback_queue, output_stream, output_rate, output_channels, interrupted_responses, first_audio_played, session_stop), name=f"rv2c-{mode}-playback"),
    ]
    global_wait = asyncio.create_task(global_stop.wait())
    switch_wait = asyncio.create_task(switch_event.wait())
    try:
        await asyncio.wait({global_wait, switch_wait}, return_when=asyncio.FIRST_COMPLETED)
        return "fallback" if switch_event.is_set() and not global_stop.is_set() else "stop"
    finally:
        session_stop.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for waiter in (global_wait, switch_wait):
            waiter.cancel()
        await asyncio.gather(global_wait, switch_wait, return_exceptions=True)


async def start_stdio_engine(args, api_key: str, env_file: Path):
    config_path, mcp_config = stdio_runner.load_mcp_config(args, env_file)
    allowed_tools = {args.mcp_server: set(args.allow_tool)} if args.allow_tool else None
    bridge = RealtimeMCPBridge(mcp_config, server_names=(args.mcp_server,), allowed_tools=allowed_tools)
    function_tools = await bridge.start()
    if not function_tools:
        await bridge.close()
        raise RuntimeError(f"no MCP tools discovered for fallback server {args.mcp_server!r}")
    print(f"RV2 AUTO fallback MCP config: {config_path}", flush=True)
    print(f"RV2 AUTO bridge tools: {len(function_tools)} discovered", flush=True)
    engine = OpenAIRealtimeEngine(
        RealtimeEngineConfig(
            provider="openai",
            model=args.model,
            voice=args.voice,
            instructions=DEFAULT_INSTRUCTIONS,
            server_vad=True,
            function_tools=function_tools,
        ),
        api_key=api_key,
    )
    await engine.start()
    await stdio_runner.wait_until_ready(engine)
    return engine, bridge


async def run(args) -> int:
    env_file = Path(args.env_file).resolve()
    load_dotenv(env_file, override=True)
    api_key = rv1.read_secret("OPENAI_API_KEY", env_file)
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY / OPENAI_API_KEY_FILE is not configured")

    native_server = native_runner.load_native_server(args, env_file)
    print("RV2 mode: auto (native first, STDIO fallback)", flush=True)
    print(f"RV2 native MCP: label={native_server.label} url={native_server.url}", flush=True)
    print("RV2 bridge startup: deferred until native failure", flush=True)

    pa = pyaudio.PyAudio()
    input_stream = output_stream = None
    native_engine = stdio_engine = None
    bridge = None
    global_stop = asyncio.Event()
    switch_event = asyncio.Event()
    state = AutoState()
    playback_queue: asyncio.Queue = asyncio.Queue()
    interrupted_responses: set[str] = set()
    first_audio_played: dict[str, float] = {}
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, global_stop.set)
        except NotImplementedError:
            pass

    timeout_task = asyncio.create_task(asyncio.sleep(args.duration))
    timeout_task.add_done_callback(lambda _task: global_stop.set())

    try:
        input_selected = args.input_device if args.input_device is not None else os.getenv("BACKEND_AUDIO_INPUT_DEVICE", "")
        output_selected = args.output_device if args.output_device is not None else os.getenv("BACKEND_AUDIO_OUTPUT_DEVICE", "")
        input_stream, input_rate, input_channels, input_frames, input_name = rv1.open_configured_input(pa, input_selected)
        output_stream, output_rate, output_channels, output_name = rv1.open_configured_output(pa, output_selected)
        print(f"RV2 input: {input_name} {input_channels}ch/{input_rate}Hz -> PCM16 mono/24000Hz", flush=True)
        print(f"RV2 output: PCM16 mono/24000Hz -> {output_name} {output_channels}ch/{output_rate}Hz", flush=True)

        native_engine = OpenAIRealtimeEngine(
            RealtimeEngineConfig(
                provider="openai",
                model=args.model,
                voice=args.voice,
                instructions=DEFAULT_INSTRUCTIONS,
                server_vad=True,
                mcp_servers=(native_server,),
            ),
            api_key=api_key,
        )
        try:
            await native_engine.start()
            ready, discovery = await wait_native_ready_and_discovery(native_engine, state)
        except Exception as exc:
            print(f"RV2 native startup exception: {exc}", flush=True)
            ready, discovery = False, "failed"

        if not ready or discovery == "failed":
            decision = classify_auto_fallback(dispatched=False, read_only=None)
            log_auto_decision(decision, trigger="native_startup_or_discovery")
            state.fallback_reason = decision.reason
            state.fallback_classification = decision.classification
            await native_engine.stop()
            native_engine = None
            stdio_engine, bridge = await start_stdio_engine(args, api_key, env_file)
            state.active_transport = "stdio"
            print("RV2 AUTO active transport: stdio/bridge (safe pre-dispatch fallback)", flush=True)
        else:
            print(f"RV2 AUTO native ready: discovery={discovery}; bridge remains stopped.", flush=True)
            state.active_transport = "native"

        if state.active_transport == "native":
            result = await run_transport_session(
                native_engine, state, "native", None,
                input_stream, input_rate, input_channels, input_frames,
                output_stream, output_rate, output_channels,
                playback_queue, interrupted_responses, first_audio_played,
                global_stop, switch_event,
            )
            if result == "fallback" and not global_stop.is_set():
                print(
                    "RV2 AUTO switching native -> stdio/bridge "
                    + json.dumps(
                        {
                            "classification": state.fallback_classification,
                            "reason": state.fallback_reason,
                            "replay": bool(state.replay_text),
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    flush=True,
                )
                rv1.clear_playback_queue(playback_queue)
                await native_engine.stop()
                native_engine = None
                switch_event = asyncio.Event()
                stdio_engine, bridge = await start_stdio_engine(args, api_key, env_file)
                state.active_transport = "stdio"
                if state.replay_text:
                    print(f"RV2 AUTO replaying safely through bridge: {state.replay_text}", flush=True)
                    await stdio_engine.send_text(state.replay_text)

        if state.active_transport == "stdio" and not global_stop.is_set():
            print("RV2 AUTO active transport: stdio/bridge; native MCP disabled for the remainder of this validation session.", flush=True)
            await run_transport_session(
                stdio_engine, state, "stdio", bridge,
                input_stream, input_rate, input_channels, input_frames,
                output_stream, output_rate, output_channels,
                playback_queue, interrupted_responses, first_audio_played,
                global_stop, asyncio.Event(),
            )
        return 0
    finally:
        global_stop.set()
        timeout_task.cancel()
        await asyncio.gather(timeout_task, return_exceptions=True)
        for engine in (native_engine, stdio_engine):
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
    parser = argparse.ArgumentParser(description="RV2C OpenAI Realtime auto MCP validation")
    parser.add_argument("--env-file", default=".env.online")
    parser.add_argument("--model", default="gpt-realtime-2.1-mini")
    parser.add_argument("--voice", default="marin")
    parser.add_argument("--duration", type=float, default=300.0)
    parser.add_argument("--mcp-config", default=None, help="MCP config override; normally use MCP_CONFIG from env")
    parser.add_argument("--mcp-server", default="mixer", help="server key in MCP_CONFIG")
    parser.add_argument("--mcp-label", default="", help="provider-facing native label override")
    parser.add_argument("--mcp-url", default="", help="native HTTPS URL override, normally Funnel /mcp")
    parser.add_argument("--mcp-header", action="append", default=[], help="extra native MCP header NAME=VALUE; repeatable")
    parser.add_argument("--mcp-authorization-env", default="", help="read native authorization token from this environment variable")
    parser.add_argument("--permission-mode", choices=("open", "approval", "restricted"), default="open")
    parser.add_argument("--allow-tool", action="append", default=[], help="optional tool allow-list; repeatable")
    parser.add_argument("--discover-only", action="store_false", dest="discover_only", default=False, help=argparse.SUPPRESS)
    parser.add_argument("--input-device", default=None, help="diagnostic override; normally use BACKEND_AUDIO_INPUT_DEVICE")
    parser.add_argument("--output-device", default=None, help="diagnostic override; normally use BACKEND_AUDIO_OUTPUT_DEVICE")
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"RV2 failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
