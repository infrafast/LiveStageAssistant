#!/usr/bin/env python3
"""RV2E OpenAI Realtime cold-vs-warm cost benchmark.

Captures one spoken mixer read request with the production-like VAD helper, then
replays the exact same PCM twice through ONE Realtime session. This isolates the
cold/session-setup turn from the marginal warm-turn cost while keeping the MCP
transport, audio, target, and user intent identical.

The benchmark is read-only and uses the configured local STDIO bridge.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time

import pyaudio
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import rv2_stdio_mcp as stdio_runner
from scripts import rv2e_classic_realtime_cost_benchmark as base
from voice_assistant.benchmark_vad_capture import capture_vad_utterance
from voice_assistant.realtime.audio import Pcm16MonoResampler, expand_pcm16_channels
from voice_assistant.realtime.engine import RealtimeEngineConfig
from voice_assistant.realtime.mcp_bridge import RealtimeMCPBridge
from voice_assistant.realtime.metrics import realtime_usage_cost_usd
from voice_assistant.realtime.openai_realtime import OpenAIRealtimeEngine
from voice_assistant.realtime.service import open_configured_output, read_secret

RATE = 24000
DEFAULT_SERVICE_ENV = "/etc/livestageassistant/.env.online"

WARM_SYSTEM_PROMPT = """You are Live Stage Assistant in a strictly read-only benchmark.
Reply in French and keep the spoken answer short. Read current mixer state through the available
function tools before answering. Never perform a write or mutation. Reuse a unique canonical target
returned by a resolver directly for the requested read; do not re-resolve or re-read identity unless
ambiguity remains. Call only the minimum tools necessary, silently, then answer once.
"""


async def wait_ready(engine: OpenAIRealtimeEngine, timeout: float = 20.0) -> None:
    while True:
        event = await asyncio.wait_for(engine.next_event(), timeout=timeout)
        if event.type == "ready":
            return
        if event.type in {"provider_error", "connection_error", "connection_closed"}:
            raise RuntimeError(f"Realtime failed before ready: {event.type} {event.data}")


async def feed_pcm(engine: OpenAIRealtimeEngine, pcm: bytes) -> float:
    chunk_bytes = int(RATE * 0.02) * 2
    for offset in range(0, len(pcm), chunk_bytes):
        await engine.send_audio(pcm[offset : offset + chunk_bytes])
        await asyncio.sleep(0.02)
    silence = b"\x00\x00" * int(RATE * 0.8)
    for offset in range(0, len(silence), chunk_bytes):
        await engine.send_audio(silence[offset : offset + chunk_bytes])
        await asyncio.sleep(0.02)
    return time.perf_counter()


async def run_turn(
    engine: OpenAIRealtimeEngine,
    bridge: RealtimeMCPBridge,
    pcm: bytes,
    output_stream,
    output_rate: int,
    output_channels: int,
    model: str,
    label: str,
) -> dict:
    post_audio_started = await feed_pcm(engine, pcm)
    transcript = ""
    answer = ""
    tool_calls: list[dict] = []
    usages: list[dict] = []
    response_costs: list[float] = []
    first_audio_ms: float | None = None
    audio_output_bytes = 0
    output_resampler = Pcm16MonoResampler(RATE, output_rate)
    deadline = time.monotonic() + 30.0

    while time.monotonic() < deadline:
        event = await asyncio.wait_for(engine.next_event(), timeout=max(0.1, deadline - time.monotonic()))
        if event.type == "user_transcript_done":
            transcript = str(event.data.get("text") or "").strip()
            if transcript:
                base.validate_fixed_query_transcript(transcript)
                print(f"RV2E_WARM_{label} transcript={transcript!r}", flush=True)
        elif event.type == "tool_call":
            name = str(event.data.get("name") or "")
            call_id = str(event.data.get("call_id") or "")
            arguments = str(event.data.get("arguments") or "{}")
            target = bridge.tool_targets.get(name)
            if target is None:
                raise RuntimeError(f"Realtime called unknown bridge function {name!r}")
            started = time.perf_counter()
            result = await bridge.execute(name, arguments)
            duration_ms = (time.perf_counter() - started) * 1000.0
            if result.get("is_error"):
                raise RuntimeError(f"Realtime MCP tool failed: {result}")
            tool_calls.append({"name": target.tool, "server": target.server, "duration_ms": round(duration_ms, 3)})
            await engine.submit_tool_result(call_id, result)
        elif event.type == "audio_delta":
            audio = event.data.get("audio") or b""
            if audio:
                if first_audio_ms is None:
                    first_audio_ms = (time.perf_counter() - post_audio_started) * 1000.0
                audio_output_bytes += len(audio)
                converted = output_resampler.process(audio)
                converted = expand_pcm16_channels(converted, output_channels)
                if converted:
                    await asyncio.to_thread(output_stream.write, converted)
        elif event.type == "transcript_done":
            text = str(event.data.get("text") or "").strip()
            if text:
                answer = text
                print(f"RV2E_WARM_{label} answer={answer!r}", flush=True)
        elif event.type == "response_done":
            usage = event.data.get("usage") or {}
            usages.append(usage)
            cost = realtime_usage_cost_usd(model, usage)
            if cost is not None:
                response_costs.append(cost)
            if answer and tool_calls:
                break
        elif event.type in {"provider_error", "connection_error", "connection_closed"}:
            raise RuntimeError(f"Realtime session failed: {event.type} {event.data}")
    else:
        raise RuntimeError(f"Realtime {label} turn timed out")

    if not transcript or not answer or not tool_calls:
        raise RuntimeError(f"Realtime {label} turn incomplete transcript={bool(transcript)} answer={bool(answer)} tools={len(tool_calls)}")

    return {
        "label": label.lower(),
        "transcript": transcript,
        "answer": answer,
        "latency_ms": {
            "post_audio_to_first_audio": round(first_audio_ms, 3) if first_audio_ms is not None else None,
            "post_audio_to_final_response": round((time.perf_counter() - post_audio_started) * 1000.0, 3),
        },
        "usage": {
            "responses": len(usages),
            "tool_calls": len(tool_calls),
            "tools": tool_calls,
            "input_tokens": sum(int(item.get("input_tokens") or 0) for item in usages),
            "output_tokens": sum(int(item.get("output_tokens") or 0) for item in usages),
            "responses_detail": usages,
            "audio_output_seconds": round(audio_output_bytes / 2.0 / RATE, 3),
        },
        "cost_usd": {
            "measured_provider_usage": sum(response_costs),
            "response_costs": response_costs,
        },
    }


async def run(args) -> int:
    env_file = Path(args.env_file).resolve()
    load_dotenv(env_file, override=True)
    api_key = read_secret("OPENAI_API_KEY", env_file)
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY / OPENAI_API_KEY_FILE is not configured")

    class BridgeArgs:
        mcp_config = args.mcp_config
        mcp_server = args.server

    config_path, raw_config = stdio_runner.load_mcp_config(BridgeArgs(), env_file)
    input_selector = str(os.getenv("BACKEND_AUDIO_INPUT_DEVICE") or "")
    output_selector = str(os.getenv("BACKEND_AUDIO_OUTPUT_DEVICE") or "")
    print("RV2E_WARM_CONFIG " + json.dumps({
        "server": args.server,
        "mcp_config": str(config_path),
        "mcp_target": base.local_mcp_target(raw_config, args.server),
        "input_selector": input_selector or "<default>",
        "output_selector": output_selector or "<default>",
    }, ensure_ascii=False, separators=(",", ":")), flush=True)

    pcm, recording = await capture_vad_utterance(0.0, input_selector)

    pa = pyaudio.PyAudio()
    output_stream = None
    bridge = RealtimeMCPBridge(base.filtered_mcp_config(raw_config, args.server), server_names=(args.server,))
    engine: OpenAIRealtimeEngine | None = None
    try:
        output_stream, output_rate, output_channels, output_name = open_configured_output(pa, output_selector)
        function_tools = await bridge.start()
        if not function_tools:
            raise RuntimeError(f"Realtime bridge discovered no tools for {args.server!r}")
        model = str(os.getenv("OPENAI_REALTIME_MODEL") or "gpt-realtime-2.1").strip()
        voice = str(os.getenv("OPENAI_REALTIME_VOICE") or "marin").strip()
        engine = OpenAIRealtimeEngine(RealtimeEngineConfig(
            provider="openai",
            model=model,
            voice=voice,
            instructions=WARM_SYSTEM_PROMPT,
            server_vad=True,
            function_tools=tuple(function_tools),
        ), api_key=api_key)
        await engine.start()
        await wait_ready(engine)

        cold = await run_turn(engine, bridge, pcm, output_stream, output_rate, output_channels, model, "COLD")
        await asyncio.sleep(0.6)
        warm = await run_turn(engine, bridge, pcm, output_stream, output_rate, output_channels, model, "WARM")

        cold_cost = float(cold["cost_usd"]["measured_provider_usage"])
        warm_cost = float(warm["cost_usd"]["measured_provider_usage"])
        report = {
            "recording": recording,
            "audio_output": {"device": output_name, "selector": output_selector, "rate": output_rate, "channels": output_channels},
            "mcp_target": base.local_mcp_target(raw_config, args.server),
            "model": model,
            "cold": cold,
            "warm": warm,
            "comparison": {
                "cold_cost_usd": cold_cost,
                "warm_cost_usd": warm_cost,
                "warm_over_cold_ratio": (warm_cost / cold_cost) if cold_cost > 0 else None,
                "warm_savings_percent": ((cold_cost - warm_cost) / cold_cost * 100.0) if cold_cost > 0 else None,
                "cold_latency_ms": cold["latency_ms"]["post_audio_to_final_response"],
                "warm_latency_ms": warm["latency_ms"]["post_audio_to_final_response"],
                "cold_tool_calls": cold["usage"]["tool_calls"],
                "warm_tool_calls": warm["usage"]["tool_calls"],
                "warm_per_100_requests_usd": warm_cost * 100.0,
                "warm_per_1000_requests_usd": warm_cost * 1000.0,
                "note": "Same PCM, same Realtime session, same local STDIO MCP. Warm cost is the marginal second-turn measurement, not a new session estimate.",
            },
        }
        print("RV2E_REALTIME_COLD_RESULT " + json.dumps(cold, ensure_ascii=False, separators=(",", ":")), flush=True)
        print("RV2E_REALTIME_WARM_RESULT " + json.dumps(warm, ensure_ascii=False, separators=(",", ":")), flush=True)
        print("RV2E_REALTIME_COLD_WARM_COMPARISON " + json.dumps(report, ensure_ascii=False, separators=(",", ":")), flush=True)
        return 0
    finally:
        if engine is not None:
            await engine.stop()
        await bridge.close()
        if output_stream is not None:
            try:
                output_stream.stop_stream()
                output_stream.close()
            except Exception:
                pass
        pa.terminate()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=DEFAULT_SERVICE_ENV)
    parser.add_argument("--mcp-config", default=None)
    parser.add_argument("--server", default="mixer")
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"RV2E_WARM failed: {type(exc).__name__}: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
