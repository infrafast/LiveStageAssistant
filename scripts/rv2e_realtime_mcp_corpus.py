#!/usr/bin/env python3
"""Run a provider-neutral Realtime MCP command corpus over the local LSA bridge.

The runner contains no domain-specific MCP knowledge. Tool expectations live in an
external JSON fixture, for example:

{
  "cases": [
    {
      "id": "read-one-state",
      "text": "user utterance for this deployment",
      "expect": {
        "min_tool_calls": 1,
        "max_tool_calls": 2,
        "required_tools": ["tool_name_from_fixture"],
        "forbidden_tools": ["broad_status_tool_if_not_needed"],
        "max_latency_ms": 4000,
        "max_cost_usd": 0.05
      }
    }
  ]
}

The same runner can validate any MCP inventory by changing only --corpus/--servers.
It uses text turns deliberately: RV2E corpus ownership is tool planning/execution,
not microphone/STT variability (covered by separate end-to-end voice benchmarks).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import rv2_stdio_mcp as stdio_runner
from voice_assistant.realtime.corpus import CorpusCase, evaluate_case, parse_case
from voice_assistant.realtime.engine import RealtimeEngineConfig
from voice_assistant.realtime.mcp_bridge import RealtimeMCPBridge
from voice_assistant.realtime.metrics import realtime_usage_cost_usd
from voice_assistant.realtime.openai_realtime import OpenAIRealtimeEngine
from voice_assistant.realtime.service import read_secret

DEFAULT_SERVICE_ENV = "/etc/livestageassistant/.env.online"

CORPUS_ADDENDUM = """This is a validation session for external tool use.
Follow the user's request exactly. Use only the minimum tool calls required to obtain or change
external state. Prefer a narrow targeted operation over a broad status/inventory operation when
the targeted operation already provides the required result or verified execution outcome.
Reuse unambiguous identifiers returned by tools in the current turn instead of rediscovering them.
Never claim external success unless a tool result confirms success. Keep the final answer concise.
"""


async def wait_ready(engine: OpenAIRealtimeEngine, timeout: float = 20.0) -> None:
    while True:
        event = await asyncio.wait_for(engine.next_event(), timeout=timeout)
        if event.type == "ready":
            return
        if event.type in {"provider_error", "connection_error", "connection_closed"}:
            raise RuntimeError(f"Realtime failed before ready: {event.type} {event.data}")


def load_cases(path: Path) -> list[CorpusCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw.get("cases") if isinstance(raw, dict) else raw
    if not isinstance(items, list) or not items:
        raise ValueError("corpus must be a non-empty list or an object with a non-empty cases list")
    cases = [parse_case(item) for item in items]
    seen: set[str] = set()
    for case in cases:
        if case.case_id in seen:
            raise ValueError(f"duplicate corpus case id: {case.case_id}")
        seen.add(case.case_id)
    return cases


async def run_case(
    engine: OpenAIRealtimeEngine,
    bridge: RealtimeMCPBridge,
    case: CorpusCase,
    model: str,
    timeout: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    await engine.send_text(case.text, create_response=True)

    answer = ""
    tools: list[dict[str, Any]] = []
    usages: list[dict[str, Any]] = []
    response_costs: list[float] = []
    response_count = 0
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        event = await asyncio.wait_for(engine.next_event(), timeout=max(0.1, deadline - time.monotonic()))
        if event.type == "tool_call":
            exposed_name = str(event.data.get("name") or "")
            call_id = str(event.data.get("call_id") or "")
            arguments = str(event.data.get("arguments") or "{}")
            target = bridge.tool_targets.get(exposed_name)
            if target is None:
                raise RuntimeError(f"unknown bridge function {exposed_name!r}")
            tool_started = time.perf_counter()
            result = await bridge.execute(exposed_name, arguments)
            tool_ms = (time.perf_counter() - tool_started) * 1000.0
            tools.append({
                "server": target.server,
                "name": target.tool,
                "duration_ms": round(tool_ms, 3),
                "is_error": bool(result.get("is_error")),
            })
            await engine.submit_tool_result(call_id, result)
        elif event.type == "transcript_done":
            text = str(event.data.get("text") or "").strip()
            if text:
                answer = text
        elif event.type == "response_done":
            response_count += 1
            usage = event.data.get("usage") or {}
            if isinstance(usage, dict):
                usages.append(usage)
                cost = realtime_usage_cost_usd(model, usage)
                if cost is not None:
                    response_costs.append(cost)
            if answer:
                break
        elif event.type in {"provider_error", "connection_error", "connection_closed"}:
            raise RuntimeError(f"Realtime corpus case {case.case_id!r} failed: {event.type} {event.data}")
    else:
        raise RuntimeError(f"Realtime corpus case {case.case_id!r} timed out")

    return {
        "id": case.case_id,
        "text": case.text,
        "answer": answer,
        "latency_ms": {"turn_to_final_response": round((time.perf_counter() - started) * 1000.0, 3)},
        "usage": {
            "responses": response_count,
            "tool_calls": len(tools),
            "tools": tools,
            "input_tokens": sum(int(item.get("input_tokens") or 0) for item in usages),
            "output_tokens": sum(int(item.get("output_tokens") or 0) for item in usages),
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

    cases = load_cases(Path(args.corpus).resolve())
    servers = tuple(part.strip() for part in args.servers.split(",") if part.strip())

    class BridgeArgs:
        mcp_config = args.mcp_config
        mcp_server = servers[0] if servers else None

    config_path, raw_config = stdio_runner.load_mcp_config(BridgeArgs(), env_file)
    bridge = RealtimeMCPBridge(raw_config, server_names=servers or None)
    engine: OpenAIRealtimeEngine | None = None
    try:
        function_tools = await bridge.start()
        if not function_tools:
            raise RuntimeError("corpus bridge discovered no tools")

        model = str(os.getenv("OPENAI_REALTIME_MODEL") or "gpt-realtime-2.1").strip()
        voice = str(os.getenv("OPENAI_REALTIME_VOICE") or "marin").strip()
        engine = OpenAIRealtimeEngine(
            RealtimeEngineConfig(
                provider="openai",
                model=model,
                voice=voice,
                instructions=CORPUS_ADDENDUM,
                server_vad=True,
                function_tools=tuple(function_tools),
            ),
            api_key=api_key,
        )
        await engine.start()
        await wait_ready(engine)

        print("RV2E_CORPUS_CONFIG " + json.dumps({
            "corpus": str(Path(args.corpus).resolve()),
            "mcp_config": str(config_path),
            "servers": list(bridge.server_names),
            "model": model,
            "cases": len(cases),
            "prompt_path": "RealtimeEngineConfig production composition + generic corpus addendum",
        }, ensure_ascii=False, separators=(",", ":")), flush=True)

        results: list[dict[str, Any]] = []
        failures = 0
        for case in cases:
            result = await run_case(engine, bridge, case, model, args.timeout)
            assertion = evaluate_case(case, result)
            result["assertion"] = {"ok": assertion.ok, "failures": list(assertion.failures)}
            if not assertion.ok:
                failures += 1
            results.append(result)
            print("RV2E_CORPUS_CASE " + json.dumps(result, ensure_ascii=False, separators=(",", ":")), flush=True)
            await asyncio.sleep(args.turn_gap)

        summary = {
            "cases": len(results),
            "passed": len(results) - failures,
            "failed": failures,
            "total_cost_usd": sum(float((item.get("cost_usd") or {}).get("measured_provider_usage") or 0.0) for item in results),
            "total_tool_calls": sum(int((item.get("usage") or {}).get("tool_calls") or 0) for item in results),
            "results": results,
        }
        print("RV2E_CORPUS_SUMMARY " + json.dumps(summary, ensure_ascii=False, separators=(",", ":")), flush=True)
        return 0 if failures == 0 else 2
    finally:
        if engine is not None:
            await engine.stop()
        await bridge.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, help="external JSON fixture; all domain/tool expectations live here")
    parser.add_argument("--env-file", default=DEFAULT_SERVICE_ENV)
    parser.add_argument("--mcp-config", default=None)
    parser.add_argument("--servers", default="", help="comma-separated MCP server names; empty means all configured servers")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--turn-gap", type=float, default=0.4)
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"RV2E_CORPUS failed: {type(exc).__name__}: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
