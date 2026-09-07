#!/usr/bin/env python3
"""Read-only OpenAI Realtime MCP benchmark: provider-native HTTPS vs LSA STDIO bridge.

The benchmark intentionally exposes only ``osc_get_mixer_status``. It never
exposes mixer write tools. Each sample creates a fresh Realtime session, sends
the same text request, and measures both request-to-tool-completion and the MCP
execution interval visible to LSA/provider events.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import statistics
import sys
import time

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import rv1_realtime_audio as rv1
from scripts import rv2_stdio_mcp as stdio_runner
from voice_assistant.realtime.engine import RealtimeEngineConfig, RealtimeMCPServer
from voice_assistant.realtime.mcp_bridge import RealtimeMCPBridge
from voice_assistant.realtime.openai_realtime import OpenAIRealtimeEngine

DEFAULT_NATIVE_URL = "https://raspberrypi-1.tail70348.ts.net/xm/mcp"
DEFAULT_TOOL = "osc_get_mixer_status"
INSTRUCTIONS = """You are running a deterministic read-only MCP latency benchmark.
Use the only exposed tool exactly once when the user asks you to run the benchmark.
Never attempt any write, mutation, adjustment, mute, level change, or retry.
Do not call a second tool. After the tool result, stop.
"""
REQUEST = "Run the read-only mixer-status benchmark now. Call the available tool exactly once."


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * p
    lo = int(position)
    hi = min(lo + 1, len(ordered) - 1)
    frac = position - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def summary(values: list[float]) -> dict:
    return {
        "n": len(values),
        "min_ms": round(min(values), 3),
        "median_ms": round(statistics.median(values), 3),
        "p95_ms": round(percentile(values, 0.95), 3),
        "max_ms": round(max(values), 3),
    }


async def wait_ready(engine: OpenAIRealtimeEngine, timeout: float, *, label: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = max(0.1, deadline - time.monotonic())
        try:
            event = await asyncio.wait_for(engine.next_event(), timeout=remaining)
        except asyncio.TimeoutError as exc:
            raise RuntimeError(f"{label} realtime ready timeout") from exc
        if event.type == "ready":
            return
        if event.type in {"provider_error", "connection_error", "connection_closed"}:
            raise RuntimeError(f"{label} Realtime failed before ready: {event.type} {event.data}")
    raise RuntimeError(f"{label} realtime ready timeout")


async def wait_native_ready_and_discovery(engine: OpenAIRealtimeEngine, tool: str, timeout: float) -> None:
    """Wait for provider READY and native MCP discovery regardless of event order."""
    ready = False
    discovered = False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = max(0.1, deadline - time.monotonic())
        try:
            event = await asyncio.wait_for(engine.next_event(), timeout=remaining)
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                f"native startup timeout: ready={ready} discovery={discovered}"
            ) from exc

        if event.type == "ready":
            ready = True
        elif event.type == "mcp_list_tools" and event.data.get("phase") == "done":
            item = event.data.get("item") or {}
            if item.get("error"):
                raise RuntimeError(f"native discovery error: {item.get('error')}")
            names = {
                str(candidate.get("name") or "")
                for candidate in item.get("tools") or []
                if isinstance(candidate, dict)
            }
            if tool not in names:
                raise RuntimeError(f"native MCP did not expose {tool!r}; got {sorted(names)}")
            discovered = True
        elif event.type in {"provider_error", "connection_error", "connection_closed"}:
            raise RuntimeError(f"native startup failed: {event.type} {event.data}")

        if ready and discovered:
            return

    raise RuntimeError(f"native startup timeout: ready={ready} discovery={discovered}")


async def run_native_sample(api_key: str, args, sample: int) -> dict:
    print(f"RV2_BENCH native sample={sample} stage=start", flush=True)
    server = RealtimeMCPServer(
        label="mixer-bench",
        url=args.native_url,
        allowed_tools=(args.tool,),
        require_approval="never",
    )
    engine = OpenAIRealtimeEngine(
        RealtimeEngineConfig(
            provider="openai",
            model=args.model,
            voice="marin",
            instructions=INSTRUCTIONS,
            server_vad=False,
            mcp_servers=(server,),
        ),
        api_key=api_key,
    )
    try:
        await engine.start()
        await wait_native_ready_and_discovery(engine, args.tool, args.timeout)
        print(f"RV2_BENCH native sample={sample} stage=ready+discovered", flush=True)

        sent_at = time.perf_counter()
        await engine.send_text(REQUEST)
        call_started = None
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            remaining = max(0.1, deadline - time.monotonic())
            try:
                event = await asyncio.wait_for(engine.next_event(), timeout=remaining)
            except asyncio.TimeoutError as exc:
                raise RuntimeError(f"native tool timeout sample={sample}") from exc
            if event.type == "mcp_call":
                item = event.data.get("item") or {}
                name = str(item.get("name") or "")
                if name and name != args.tool:
                    raise RuntimeError(f"native called unexpected tool {name!r}")
                if event.data.get("phase") == "added":
                    if call_started is not None:
                        raise RuntimeError("native attempted more than one MCP call")
                    call_started = time.perf_counter()
                elif event.data.get("phase") == "done":
                    finished = time.perf_counter()
                    if item.get("error"):
                        raise RuntimeError(f"native tool error: {item.get('error')}")
                    if call_started is None:
                        raise RuntimeError("native tool completed without observed start")
                    result = {
                        "transport": "native",
                        "sample": sample,
                        "tool": args.tool,
                        "tool_ms": round((finished - call_started) * 1000.0, 3),
                        "request_to_tool_done_ms": round((finished - sent_at) * 1000.0, 3),
                    }
                    print("RV2_BENCH " + json.dumps(result, separators=(",", ":")), flush=True)
                    return result
            if event.type in {"provider_error", "connection_error", "connection_closed"}:
                raise RuntimeError(f"native session failed: {event.type} {event.data}")
        raise RuntimeError(f"native tool timeout sample={sample}")
    finally:
        await engine.stop()


async def run_stdio_sample(api_key: str, env_file: Path, args, sample: int) -> dict:
    print(f"RV2_BENCH stdio sample={sample} stage=start", flush=True)

    class BridgeArgs:
        mcp_config = args.mcp_config
        mcp_server = args.server

    _, config = stdio_runner.load_mcp_config(BridgeArgs(), env_file)
    bridge = RealtimeMCPBridge(
        config,
        server_names=(args.server,),
        allowed_tools={args.server: {args.tool}},
    )
    engine = None
    try:
        function_tools = await bridge.start()
        matching = [
            name
            for name, target in bridge.tool_targets.items()
            if target.server == args.server and target.tool == args.tool
        ]
        if len(matching) != 1:
            raise RuntimeError(f"STDIO bridge expected one mapping for {args.tool!r}, got {matching}")
        function_name = matching[0]
        function_tools = tuple(tool for tool in function_tools if tool.name == function_name)
        if len(function_tools) != 1:
            raise RuntimeError(f"STDIO bridge did not expose function {function_name!r}")

        engine = OpenAIRealtimeEngine(
            RealtimeEngineConfig(
                provider="openai",
                model=args.model,
                voice="marin",
                instructions=INSTRUCTIONS,
                server_vad=False,
                function_tools=function_tools,
            ),
            api_key=api_key,
        )
        await engine.start()
        await wait_ready(engine, args.timeout, label="stdio")
        print(f"RV2_BENCH stdio sample={sample} stage=ready", flush=True)

        sent_at = time.perf_counter()
        await engine.send_text(REQUEST)
        seen_call = False
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            remaining = max(0.1, deadline - time.monotonic())
            try:
                event = await asyncio.wait_for(engine.next_event(), timeout=remaining)
            except asyncio.TimeoutError as exc:
                raise RuntimeError(f"STDIO tool timeout sample={sample}") from exc
            if event.type == "tool_call":
                if seen_call:
                    raise RuntimeError("STDIO attempted more than one tool call")
                seen_call = True
                if str(event.data.get("name") or "") != function_name:
                    raise RuntimeError(f"STDIO called unexpected function {event.data.get('name')!r}")
                started = time.perf_counter()
                result = await bridge.execute(function_name, str(event.data.get("arguments") or "{}"))
                finished = time.perf_counter()
                if result.get("is_error"):
                    raise RuntimeError(f"STDIO tool error: {result}")
                record = {
                    "transport": "stdio",
                    "sample": sample,
                    "tool": args.tool,
                    "tool_ms": round((finished - started) * 1000.0, 3),
                    "request_to_tool_done_ms": round((finished - sent_at) * 1000.0, 3),
                }
                print("RV2_BENCH " + json.dumps(record, separators=(",", ":")), flush=True)
                return record
            if event.type in {"provider_error", "connection_error", "connection_closed"}:
                raise RuntimeError(f"STDIO session failed: {event.type} {event.data}")
        raise RuntimeError(f"STDIO tool timeout sample={sample}")
    finally:
        if engine is not None:
            await engine.stop()
        await bridge.close()


async def run(args) -> int:
    env_file = Path(args.env_file).resolve()
    load_dotenv(env_file, override=True)
    api_key = rv1.read_secret("OPENAI_API_KEY", env_file)
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY / OPENAI_API_KEY_FILE is not configured")
    if not args.native_url.lower().startswith("https://"):
        raise RuntimeError("--native-url must be an externally reachable HTTPS MCP URL")

    print(
        "RV2_BENCH_CONFIG "
        + json.dumps(
            {
                "model": args.model,
                "native_url": args.native_url,
                "server": args.server,
                "tool": args.tool,
                "samples": args.samples,
                "timeout": args.timeout,
            },
            separators=(",", ":"),
        ),
        flush=True,
    )

    native: list[dict] = []
    stdio: list[dict] = []
    for sample in range(1, args.samples + 1):
        native.append(await run_native_sample(api_key, args, sample))
        stdio.append(await run_stdio_sample(api_key, env_file, args, sample))

    report = {
        "tool": args.tool,
        "samples": args.samples,
        "native": {
            "tool": summary([row["tool_ms"] for row in native]),
            "request_to_tool_done": summary([row["request_to_tool_done_ms"] for row in native]),
        },
        "stdio": {
            "tool": summary([row["tool_ms"] for row in stdio]),
            "request_to_tool_done": summary([row["request_to_tool_done_ms"] for row in stdio]),
        },
    }
    report["median_delta_ms_native_minus_stdio"] = round(
        report["native"]["request_to_tool_done"]["median_ms"]
        - report["stdio"]["request_to_tool_done"]["median_ms"],
        3,
    )
    print("RV2_BENCH_SUMMARY " + json.dumps(report, separators=(",", ":"), ensure_ascii=False), flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env.online")
    parser.add_argument("--mcp-config", default=None)
    parser.add_argument("--server", default="mixer")
    parser.add_argument("--tool", default=DEFAULT_TOOL)
    parser.add_argument("--native-url", default=os.getenv("RV2_BENCH_NATIVE_URL", DEFAULT_NATIVE_URL))
    parser.add_argument("--model", default=os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime-2.1"))
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples must be >= 1")
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"RV2_BENCH failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
