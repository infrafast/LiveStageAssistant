#!/usr/bin/env python3
"""RV2B OpenAI Realtime -> existing LSA MCP client/STDIO bridge runner.

This runner is intentionally stdio/bridge-only. The realtime provider receives
ordinary function tools discovered through the existing mcp-use MCPClient. It
never configures provider-native remote MCP and therefore never uses a Funnel URL.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import time

import pyaudio
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import rv1_realtime_audio as rv1
from voice_assistant.realtime.engine import RealtimeEngineConfig
from voice_assistant.realtime.mcp_bridge import RealtimeMCPBridge
from voice_assistant.realtime.metrics import realtime_usage_cost_usd
from voice_assistant.realtime.openai_realtime import OpenAIRealtimeEngine


RV2_METRICS_PREFIX = "RV2_METRICS "
DEFAULT_INSTRUCTIONS = """You are Live Stage Assistant in RV2B STDIO bridge validation.
Follow the language of the user's latest utterance. If unclear, default to English.
Keep spoken replies concise unless the user explicitly asks for detail.
Use the function tools whenever needed to inspect or control the connected system.
The functions are MCP tools executed through the local LSA bridge. Never invent a tool result.
When a tool is needed, do not speak a waiting/filler sentence first. Call the tool, wait for its result, then answer once with the verified result.
Never claim an external action succeeded unless the tool result confirms it.
When interrupted, abandon the previous spoken response and handle only the new utterance.
If the user only asks you to stop speaking or be silent, stop without a spoken acknowledgement.
"""


def resolve_path(value: str, env_file: Path) -> Path:
    path = Path(os.path.expandvars(value)).expanduser()
    if path.is_absolute():
        return path
    candidates = [env_file.parent / path, ROOT / path]
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[-1])


def load_mcp_config(args, env_file: Path) -> tuple[Path, dict]:
    config_value = (args.mcp_config or os.getenv("MCP_CONFIG") or "mcp_servers.json").strip()
    config_path = resolve_path(config_value, env_file)
    if not config_path.is_file():
        raise RuntimeError(f"MCP config not found: {config_path}")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not read MCP config {config_path}: {exc}") from exc
    servers = config.get("mcpServers") or {}
    if args.mcp_server not in servers:
        raise RuntimeError(f"MCP server {args.mcp_server!r} not found in {config_path}")
    return config_path, config


def bridge_result_summary(result: dict) -> dict:
    return {
        "transport": result.get("transport"),
        "server": result.get("server"),
        "tool": result.get("tool"),
        "is_error": result.get("is_error"),
        "content": result.get("content"),
        "structured_content": result.get("structured_content"),
    }


async def wait_until_ready(engine: OpenAIRealtimeEngine) -> None:
    while True:
        event = await asyncio.wait_for(engine.next_event(), timeout=20.0)
        if event.type == "ready":
            return
        if event.type in {"provider_error", "connection_error", "connection_closed"}:
            raise RuntimeError(f"Realtime session failed before ready: {event.data}")


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
        "RV2 bridge call "
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
        "RV2 bridge call "
        + json.dumps(
            {"phase": "done", "call_id": call_id, "duration_ms": duration_ms, **bridge_result_summary(result)},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        flush=True,
    )
    await engine.submit_tool_result(call_id, result)
    print(f"RV2 bridge result submitted: call_id={call_id}", flush=True)


async def event_loop(engine, bridge, playback_queue, interrupted_responses, first_audio_played, stop_event) -> None:
    current_response_id = ""
    speech_stopped_at: float | None = None
    response_started_at: dict[str, float] = {}
    speech_stop_by_response: dict[str, float] = {}
    first_audio_received: dict[str, float] = {}
    completed_response_ids: set[str] = set()
    completed_calls: set[str] = set()
    tool_tasks: set[asyncio.Task] = set()
    turn_index = 0

    while not stop_event.is_set():
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
                print(f"Utilisateur: {text}", flush=True)
        elif event.type == "user_transcript_error":
            print("RV2 user transcription error: " + json.dumps(event.data, ensure_ascii=False), flush=True)
        elif event.type == "response_started":
            response = event.data.get("response") or {}
            current_response_id = str(response.get("id") or "")
            response_started_at[current_response_id] = now
            if speech_stopped_at is not None:
                speech_stop_by_response[current_response_id] = speech_stopped_at
        elif event.type == "tool_call":
            task = asyncio.create_task(
                execute_bridge_call(engine, bridge, event, completed_calls),
                name=f"rv2b-tool-{event.data.get('call_id') or 'unknown'}",
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
                "mcp_transport": "stdio/bridge",
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
        elif event.type in {"provider_error", "connection_error"}:
            print(f"RV2 provider error: {event.data}", flush=True)
        elif event.type == "connection_closed":
            print("RV2 connection closed", flush=True)
            stop_event.set()
            break

    for task in tuple(tool_tasks):
        task.cancel()
    if tool_tasks:
        await asyncio.gather(*tool_tasks, return_exceptions=True)


async def run(args) -> int:
    env_file = Path(args.env_file).resolve()
    load_dotenv(env_file, override=True)
    api_key = rv1.read_secret("OPENAI_API_KEY", env_file)
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY / OPENAI_API_KEY_FILE is not configured")

    config_path, mcp_config = load_mcp_config(args, env_file)
    allowed_tools = None
    if args.allow_tool:
        allowed_tools = {args.mcp_server: set(args.allow_tool)}
    bridge = RealtimeMCPBridge(
        mcp_config,
        server_names=(args.mcp_server,),
        allowed_tools=allowed_tools,
    )
    engine = None
    pa = pyaudio.PyAudio()
    input_stream = output_stream = None
    stop_event = asyncio.Event()
    tasks: list[asyncio.Task] = []
    playback_queue: asyncio.Queue = asyncio.Queue()
    interrupted_responses: set[str] = set()
    first_audio_played: dict[str, float] = {}

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    try:
        function_tools = await bridge.start()
        if not function_tools:
            raise RuntimeError(f"no MCP tools discovered for bridge server {args.mcp_server!r}")
        print("RV2 mode: stdio only", flush=True)
        print(f"RV2 MCP config: {config_path}", flush=True)
        print(f"RV2 bridge server: {args.mcp_server}", flush=True)
        print("RV2 native MCP: disabled", flush=True)
        print(f"RV2 bridge tools: {len(function_tools)} discovered", flush=True)
        print(
            "RV2 bridge tool names: " + ", ".join(target.tool for target in bridge.tool_targets.values()),
            flush=True,
        )

        input_selected = args.input_device if args.input_device is not None else os.getenv("BACKEND_AUDIO_INPUT_DEVICE", "")
        output_selected = args.output_device if args.output_device is not None else os.getenv("BACKEND_AUDIO_OUTPUT_DEVICE", "")
        input_stream, input_rate, input_channels, input_frames, input_name = rv1.open_configured_input(pa, input_selected)
        output_stream, output_rate, output_channels, output_name = rv1.open_configured_output(pa, output_selected)
        print(f"RV2 input: {input_name} {input_channels}ch/{input_rate}Hz -> PCM16 mono/24000Hz", flush=True)
        print(f"RV2 output: PCM16 mono/24000Hz -> {output_name} {output_channels}ch/{output_rate}Hz", flush=True)

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
        await wait_until_ready(engine)
        print(f"RV2 connected: model={args.model} voice={args.voice}; STDIO bridge enabled, native MCP disabled.", flush=True)
        print("Speak naturally. MCP calls execute through the existing local LSA client path. Ctrl+C to stop.", flush=True)

        tasks = [
            asyncio.create_task(rv1.capture_loop(engine, input_stream, input_rate, input_channels, input_frames, stop_event), name="rv2b-capture"),
            asyncio.create_task(event_loop(engine, bridge, playback_queue, interrupted_responses, first_audio_played, stop_event), name="rv2b-events"),
            asyncio.create_task(rv1.playback_loop(playback_queue, output_stream, output_rate, output_channels, interrupted_responses, first_audio_played, stop_event), name="rv2b-playback"),
        ]
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=args.duration)
        except asyncio.TimeoutError:
            stop_event.set()
        return 0
    finally:
        stop_event.set()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if engine is not None:
            await engine.stop()
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
    parser = argparse.ArgumentParser(description="RV2B OpenAI Realtime STDIO/LSA MCP bridge validation")
    parser.add_argument("--env-file", default=".env.online")
    parser.add_argument("--model", default="gpt-realtime-2.1-mini")
    parser.add_argument("--voice", default="marin")
    parser.add_argument("--duration", type=float, default=300.0)
    parser.add_argument("--mcp-config", default=None, help="MCP config override; normally use MCP_CONFIG from env")
    parser.add_argument("--mcp-server", default="mixer", help="server key in MCP_CONFIG")
    parser.add_argument("--allow-tool", action="append", default=[], help="optional restricted tool allow-list; repeatable")
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
