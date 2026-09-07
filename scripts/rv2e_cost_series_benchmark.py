#!/usr/bin/env python3
"""Repeated Classic-vs-Realtime transaction cost benchmark.

Capture one validated spoken request once, reuse the exact PCM for N Classic
transactions and N Realtime transactions. Realtime transactions share one
session so turn 1 is cold and subsequent turns are warm. The runner reports
median/p95 latency and provider cost plus per-100/per-1000 extrapolations.

The fixed spoken query is "Quel est le volume de clic ?". The benchmark remains
read-only and MCP-neutral at the LSA layer; the selected MCP server and its own
prompt/tools define domain behavior.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import statistics
import sys

import pyaudio
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import rv2_stdio_mcp as stdio_runner
from scripts import rv2e_classic_realtime_cost_benchmark as base
from scripts import rv2e_realtime_warm_cost_benchmark as warm
from voice_assistant.benchmark_vad_capture import capture_vad_utterance
from voice_assistant.realtime.engine import RealtimeEngineConfig
from voice_assistant.realtime.mcp_bridge import RealtimeMCPBridge
from voice_assistant.realtime.openai_realtime import OpenAIRealtimeEngine
from voice_assistant.realtime.service import open_configured_output, read_secret

DEFAULT_SERVICE_ENV = "/etc/livestageassistant/.env.online"


def validate_series_transcript(text: str) -> None:
    """Accept only expected ASR spellings of the fixed target name clic.

    This is benchmark-local and does not add domain semantics to production LSA.
    """
    normalized = " ".join(
        text.casefold()
        .replace("-", " ")
        .replace("_", " ")
        .replace("?", " ")
        .replace(".", " ")
        .replace(",", " ")
        .split()
    )
    words = set(normalized.split())
    target_variants = {"clic", "click", "clique"}
    if "volume" not in words or not words.intersection(target_variants):
        raise RuntimeError(
            "recorded speech was not recognized as the fixed transaction benchmark query; "
            f"got {text!r}. No cost comparison produced."
        )


# Both base.run_classic() and warm.run_turn() call the shared validator from the
# imported benchmark module. Override it only inside this benchmark process so
# the exact same ASR acceptance rule applies to both pipelines.
base.validate_fixed_query_transcript = validate_series_transcript


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(len(ordered) - 1, lo + 1)
    frac = pos - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "median": None, "p95": None, "max": None, "mean": None}
    return {
        "min": min(values),
        "median": statistics.median(values),
        "p95": percentile(values, 0.95),
        "max": max(values),
        "mean": statistics.fmean(values),
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

    print("RV2E_SERIES_CONFIG " + json.dumps({
        "samples": args.samples,
        "server": args.server,
        "mcp_config": str(config_path),
        "transport": "stdio/local",
        "input_selector": input_selector or "<default>",
        "output_selector": output_selector or "<default>",
    }, ensure_ascii=False, separators=(",", ":")), flush=True)

    pcm, recording = await capture_vad_utterance(0.0, input_selector)
    audio_seconds = len(pcm) / 2.0 / base.RATE

    pa = pyaudio.PyAudio()
    output_stream = None
    rt_engine: OpenAIRealtimeEngine | None = None
    rt_bridge: RealtimeMCPBridge | None = None
    try:
        output_stream, output_rate, output_channels, output_name = open_configured_output(pa, output_selector)

        classic_results: list[dict] = []
        for index in range(args.samples):
            result = await base.run_classic(
                api_key,
                raw_config,
                args.server,
                pcm,
                audio_seconds,
                output_stream,
                output_rate,
                output_channels,
            )
            result["sample"] = index + 1
            classic_results.append(result)
            print("RV2E_SERIES_CLASSIC " + json.dumps(result, ensure_ascii=False, separators=(",", ":")), flush=True)
            await asyncio.sleep(args.turn_gap)

        rt_bridge = RealtimeMCPBridge(base.filtered_mcp_config(raw_config, args.server), server_names=(args.server,))
        tools = await rt_bridge.start()
        if not tools:
            raise RuntimeError("Realtime bridge discovered no tools")
        model = str(os.getenv("OPENAI_REALTIME_MODEL") or "gpt-realtime-2.1").strip()
        voice = str(os.getenv("OPENAI_REALTIME_VOICE") or "marin").strip()
        rt_engine = OpenAIRealtimeEngine(
            RealtimeEngineConfig(
                provider="openai",
                model=model,
                voice=voice,
                instructions=warm.BENCHMARK_ADDENDUM,
                server_vad=True,
                function_tools=tuple(tools),
            ),
            api_key=api_key,
        )
        await rt_engine.start()
        await warm.wait_ready(rt_engine)

        realtime_results: list[dict] = []
        for index in range(args.samples):
            label = "COLD" if index == 0 else f"WARM{index}"
            result = await warm.run_turn(
                rt_engine,
                rt_bridge,
                pcm,
                output_stream,
                output_rate,
                output_channels,
                model,
                label,
            )
            result["sample"] = index + 1
            realtime_results.append(result)
            print("RV2E_SERIES_REALTIME " + json.dumps(result, ensure_ascii=False, separators=(",", ":")), flush=True)
            await asyncio.sleep(args.turn_gap)

        classic_costs = [float(item["cost_usd"]["total_with_tts_estimate"]) for item in classic_results]
        classic_lat = [float(item["latency_ms"]["post_capture_total"]) for item in classic_results]
        rt_costs = [float(item["cost_usd"]["measured_provider_usage"]) for item in realtime_results]
        rt_lat = [float(item["latency_ms"]["post_audio_to_final_response"]) for item in realtime_results]
        warm_costs = rt_costs[1:]
        warm_lat = rt_lat[1:]

        classic_cost_stats = stats(classic_costs)
        rt_all_cost_stats = stats(rt_costs)
        rt_warm_cost_stats = stats(warm_costs)
        classic_latency_stats = stats(classic_lat)
        rt_all_latency_stats = stats(rt_lat)
        rt_warm_latency_stats = stats(warm_lat)

        classic_median_cost = float(classic_cost_stats["median"] or 0.0)
        warm_median_cost = float(rt_warm_cost_stats["median"] or 0.0)
        report = {
            "recording": recording,
            "samples": args.samples,
            "classic": {
                "cost_usd": classic_cost_stats,
                "latency_ms": classic_latency_stats,
                "per_100_from_median_usd": classic_median_cost * 100.0,
                "per_1000_from_median_usd": classic_median_cost * 1000.0,
            },
            "realtime_all": {
                "cost_usd": rt_all_cost_stats,
                "latency_ms": rt_all_latency_stats,
                "cold_cost_usd": rt_costs[0] if rt_costs else None,
            },
            "realtime_warm": {
                "samples": len(warm_costs),
                "cost_usd": rt_warm_cost_stats,
                "latency_ms": rt_warm_latency_stats,
                "per_100_from_median_usd": warm_median_cost * 100.0,
                "per_1000_from_median_usd": warm_median_cost * 1000.0,
            },
            "comparison": {
                "warm_realtime_over_classic_median_cost_ratio": (warm_median_cost / classic_median_cost) if classic_median_cost else None,
                "classic_tool_calls": [int(item.get("usage", {}).get("llm_calls") or 0) for item in classic_results],
                "realtime_tool_calls": [int(item.get("usage", {}).get("tool_calls") or 0) for item in realtime_results],
                "note": "Classic includes measured Whisper + measured LLM tokens + TTS estimate from exact returned PCM duration. Realtime uses measured provider usage. Realtime sample 1 is cold; samples 2..N are warm turns in the same session.",
            },
        }
        print("RV2E_TRANSACTION_COST_SUMMARY " + json.dumps(report, ensure_ascii=False, separators=(",", ":")), flush=True)
        return 0
    finally:
        if rt_engine is not None:
            await rt_engine.stop()
        if rt_bridge is not None:
            await rt_bridge.close()
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
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--turn-gap", type=float, default=0.5)
    args = parser.parse_args()
    if args.samples < 3:
        parser.error("--samples must be >= 3")
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"RV2E_SERIES failed: {type(exc).__name__}: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())