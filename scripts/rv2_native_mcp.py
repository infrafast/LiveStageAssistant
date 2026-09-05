#!/usr/bin/env python3
"""RV2A OpenAI Realtime native remote MCP validation runner.

This runner is intentionally native-only: OpenAI Realtime talks directly to one
remote HTTPS MCP server. It never starts or falls back to the LSA STDIO bridge.

Live/default behavior is permissive: all MCP tools are exposed with no approval
requirement. Optional diagnostic permission modes mirror the future per-MCP GUI
policy: open, approval, restricted. --discover-only remains a safe diagnostic
mode that blocks execution by requiring approval while the runner never approves.
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
from voice_assistant.realtime.engine import RealtimeEngineConfig, RealtimeMCPServer
from voice_assistant.realtime.metrics import realtime_usage_cost_usd
from voice_assistant.realtime.openai_realtime import OpenAIRealtimeEngine


RV2_METRICS_PREFIX = "RV2_METRICS "
DEFAULT_INSTRUCTIONS = """You are Live Stage Assistant in the RV2A native remote MCP validation.
Follow the language of the user's latest utterance. If unclear, default to English.
Keep spoken replies concise unless the user explicitly asks for detail.
Use the remote MCP server and tools exposed in this session whenever needed.
Never invent a tool result and never claim an external action succeeded unless the MCP result confirms it.
When interrupted, abandon the previous response and handle only the new utterance.
If the user only asks you to stop speaking or be silent, stop without a spoken acknowledgement.
"""
DISCOVERY_INSTRUCTIONS = DEFAULT_INSTRUCTIONS + """
This session is discovery-only. Do not execute any MCP tool. You may describe available tool metadata, but never approve or perform a tool call. If a tool call would be needed, say that discovery mode prevents execution.
"""


def resolve_path(value: str, env_file: Path) -> Path:
    path = Path(os.path.expandvars(value)).expanduser()
    if path.is_absolute():
        return path
    candidates = [env_file.parent / path, ROOT / path]
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[-1])


def expand_mapping(values: dict) -> dict[str, str]:
    return {str(key): os.path.expandvars(str(value)) for key, value in values.items()}


def split_authorization(headers: dict[str, str]) -> tuple[str, dict[str, str]]:
    clean = dict(headers)
    authorization = ""
    for key in list(clean):
        if key.casefold() != "authorization":
            continue
        value = clean.pop(key).strip()
        authorization = value[7:].strip() if value.casefold().startswith("bearer ") else value
        break
    return authorization, clean


def load_config_entry(args, env_file: Path) -> tuple[Path, dict]:
    config_value = (args.mcp_config or os.getenv("MCP_CONFIG") or "mcp_servers.json").strip()
    config_path = resolve_path(config_value, env_file)
    if not config_path.is_file():
        if args.mcp_url:
            return config_path, {}
        raise RuntimeError(f"MCP config not found: {config_path}")
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not read MCP config {config_path}: {exc}") from exc
    servers = payload.get("mcpServers") or {}
    entry = servers.get(args.mcp_server)
    if not isinstance(entry, dict):
        if args.mcp_url:
            return config_path, {}
        raise RuntimeError(f"MCP server {args.mcp_server!r} not found in {config_path}")
    return config_path, entry


def permission_policy(args) -> tuple[tuple[str, ...], str]:
    if args.discover_only:
        return (), "always"
    if args.permission_mode == "open":
        return (), "never"
    if args.permission_mode == "approval":
        return (), "always"
    allowed = tuple(dict.fromkeys(tool.strip() for tool in args.allow_tool if tool.strip()))
    if not allowed:
        raise RuntimeError("permission mode 'restricted' requires at least one --allow-tool")
    return allowed, "never"


def load_native_server(args, env_file: Path) -> RealtimeMCPServer:
    config_path, entry = load_config_entry(args, env_file)
    if entry:
        print(f"RV2 MCP config: {config_path}", flush=True)

    configured_url = str(entry.get("url") or "").strip()
    url = (args.mcp_url or configured_url).strip()
    label = args.mcp_label.strip() or args.mcp_server.strip()
    headers = expand_mapping(entry.get("headers") or {})

    for raw in args.mcp_header:
        if "=" not in raw:
            raise RuntimeError(f"invalid --mcp-header {raw!r}; expected NAME=VALUE")
        key, value = raw.split("=", 1)
        headers[key.strip()] = os.path.expandvars(value.strip())

    if not url.lower().startswith("https://"):
        raise RuntimeError(
            f"native mode requires an externally reachable HTTPS MCP URL, got {url!r}; "
            "use --mcp-url with the Tailscale Funnel/HTTPS endpoint"
        )

    authorization, headers = split_authorization(headers)
    if args.mcp_authorization_env:
        authorization = (os.getenv(args.mcp_authorization_env) or "").strip()
        if not authorization:
            raise RuntimeError(f"environment variable {args.mcp_authorization_env!r} is empty")

    allowed_tools, require_approval = permission_policy(args)
    return RealtimeMCPServer(
        label=label,
        url=url,
        authorization=authorization,
        headers=headers,
        allowed_tools=allowed_tools,
        require_approval=require_approval,
    )


def safe_tool_summary(tool: dict) -> dict:
    annotations = tool.get("annotations") or {}
    return {
        "name": tool.get("name"),
        "description": tool.get("description"),
        "readOnlyHint": annotations.get("readOnlyHint"),
    }


def print_mcp_event(event, call_started: dict[str, float]) -> None:
    phase = event.data.get("phase") or "event"
    item = event.data.get("item") or {}
    if event.type == "mcp_list_tools":
        tools = item.get("tools") or []
        summaries = [safe_tool_summary(tool) for tool in tools if isinstance(tool, dict)]
        print(
            "RV2 MCP tools "
            + json.dumps(
                {
                    "phase": phase,
                    "server_label": item.get("server_label"),
                    "count": len(summaries),
                    "tools": summaries,
                    "error": item.get("error"),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            flush=True,
        )
        return

    if event.type == "mcp_call":
        call_id = str(item.get("id") or item.get("call_id") or "")
        now = time.perf_counter()
        if phase == "added" and call_id:
            call_started[call_id] = now
        elapsed = None
        if phase == "done" and call_id in call_started:
            elapsed = round((now - call_started.pop(call_id)) * 1000.0, 3)
        print(
            "RV2 MCP call "
            + json.dumps(
                {
                    "phase": phase,
                    "server_label": item.get("server_label"),
                    "name": item.get("name"),
                    "arguments": item.get("arguments"),
                    "output": item.get("output"),
                    "approval_request_id": item.get("approval_request_id"),
                    "error": item.get("error"),
                    "duration_ms": elapsed,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            flush=True,
        )
        return

    if event.type == "mcp_approval_request":
        print(
            "RV2 MCP approval requested "
            + json.dumps(
                {
                    "phase": phase,
                    "server_label": item.get("server_label"),
                    "name": item.get("name"),
                    "arguments": item.get("arguments"),
                    "id": item.get("id"),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            flush=True,
        )
        return

    raw = event.data.get("event") or event.data
    print("RV2 MCP event " + json.dumps(raw, ensure_ascii=False, separators=(",", ":")), flush=True)


async def wait_until_ready(engine: OpenAIRealtimeEngine) -> None:
    while True:
        event = await asyncio.wait_for(engine.next_event(), timeout=20.0)
        if event.type == "ready":
            return
        if event.type in {"provider_error", "connection_error", "connection_closed"}:
            raise RuntimeError(f"Realtime session failed before ready: {event.data}")
        if event.type.startswith("mcp_"):
            print_mcp_event(event, {})


async def event_loop(engine, playback_queue, interrupted_responses, first_audio_played, stop_event) -> None:
    current_response_id = ""
    speech_stopped_at: float | None = None
    response_started_at: dict[str, float] = {}
    speech_stop_by_response: dict[str, float] = {}
    first_audio_received: dict[str, float] = {}
    completed_response_ids: set[str] = set()
    call_started: dict[str, float] = {}
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
        elif event.type in {"mcp_list_tools", "mcp_call", "mcp_approval_request", "mcp_event"}:
            print_mcp_event(event, call_started)
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
                "mcp_transport": "native",
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
            return


async def run(args) -> int:
    env_file = Path(args.env_file).resolve()
    load_dotenv(env_file, override=True)
    api_key = rv1.read_secret("OPENAI_API_KEY", env_file)
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY / OPENAI_API_KEY_FILE is not configured")

    server = load_native_server(args, env_file)
    print("RV2 mode: native only", flush=True)
    print(f"RV2 MCP server: label={server.label} url={server.url}", flush=True)
    print(f"RV2 MCP auth: {'configured' if server.authorization or server.headers else 'none'}", flush=True)
    print(f"RV2 permission mode: {'discovery' if args.discover_only else args.permission_mode}", flush=True)
    print(f"RV2 allowed tools: {list(server.allowed_tools) if server.allowed_tools else '<all discovered>'}", flush=True)
    print(f"RV2 approval: {server.require_approval}", flush=True)

    pa = pyaudio.PyAudio()
    input_stream = output_stream = None
    engine = None
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
        input_selected = args.input_device if args.input_device is not None else os.getenv("BACKEND_AUDIO_INPUT_DEVICE", "")
        output_selected = args.output_device if args.output_device is not None else os.getenv("BACKEND_AUDIO_OUTPUT_DEVICE", "")
        input_stream, input_rate, input_channels, input_frames, input_name = rv1.open_configured_input(pa, input_selected)
        output_stream, output_rate, output_channels, output_name = rv1.open_configured_output(pa, output_selected)
        print(f"RV2 input: {input_name} {input_channels}ch/{input_rate}Hz -> PCM16 mono/24000Hz", flush=True)
        print(f"RV2 output: PCM16 mono/24000Hz -> {output_name} {output_channels}ch/{output_rate}Hz", flush=True)

        config = RealtimeEngineConfig(
            provider="openai",
            model=args.model,
            voice=args.voice,
            instructions=DISCOVERY_INSTRUCTIONS if args.discover_only else DEFAULT_INSTRUCTIONS,
            server_vad=True,
            mcp_servers=(server,),
        )
        engine = OpenAIRealtimeEngine(config, api_key=api_key)
        await engine.start()
        await wait_until_ready(engine)
        print(f"RV2 connected: model={args.model} voice={args.voice}; native MCP enabled.", flush=True)
        if args.discover_only:
            print("Discovery-only safety is active: every MCP call requires approval and this runner never approves calls.", flush=True)
            print("Ask which tools are available, but do not request an equipment action. Ctrl+C to stop.", flush=True)
        elif args.permission_mode == "open":
            print("Live permission policy: all discovered MCP tools are available without per-call approval.", flush=True)
        elif args.permission_mode == "approval":
            print("Approval policy active: MCP calls require approval; this validation runner does not auto-approve them.", flush=True)
        else:
            print("Restricted policy active: only the explicitly allowed MCP tools are available.", flush=True)

        tasks = [
            asyncio.create_task(rv1.capture_loop(engine, input_stream, input_rate, input_channels, input_frames, stop_event), name="rv2-capture"),
            asyncio.create_task(event_loop(engine, playback_queue, interrupted_responses, first_audio_played, stop_event), name="rv2-events"),
            asyncio.create_task(rv1.playback_loop(playback_queue, output_stream, output_rate, output_channels, interrupted_responses, first_audio_played, stop_event), name="rv2-playback"),
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
        for stream in (input_stream, output_stream):
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
        pa.terminate()


def main() -> int:
    parser = argparse.ArgumentParser(description="RV2A OpenAI Realtime native remote MCP validation")
    parser.add_argument("--env-file", default=".env.online")
    parser.add_argument("--model", default="gpt-realtime-2.1-mini")
    parser.add_argument("--voice", default="marin")
    parser.add_argument("--duration", type=float, default=180.0)
    parser.add_argument("--mcp-config", default=None, help="MCP config override; normally use MCP_CONFIG from the env profile")
    parser.add_argument("--mcp-server", default="mixer", help="server key in MCP_CONFIG")
    parser.add_argument("--mcp-label", default="", help="provider-facing label override")
    parser.add_argument("--mcp-url", default="", help="native HTTPS diagnostic override, e.g. the Funnel /mcp URL")
    parser.add_argument("--mcp-header", action="append", default=[], help="extra remote MCP header NAME=VALUE; repeatable")
    parser.add_argument("--mcp-authorization-env", default="", help="read native MCP authorization token from this environment variable")
    parser.add_argument("--permission-mode", choices=("open", "approval", "restricted"), default="open", help="per-MCP permission policy; open is the live default")
    parser.add_argument("--allow-tool", action="append", default=[], help="tool allow-list entry for --permission-mode restricted; repeatable")
    parser.add_argument("--discover-only", action="store_true", help="safe discovery mode: require approval for every MCP call; runner never approves")
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
