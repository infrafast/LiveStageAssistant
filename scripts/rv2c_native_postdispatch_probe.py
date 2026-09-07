#!/usr/bin/env python3
"""Provider-native RV2C ambiguous post-dispatch mutation probe.

This is intentionally isolated from stage hardware. It connects OpenAI Realtime
only to the RV2C fault fixture, asks the provider to invoke exactly one fixture
mutation, and routes the resulting native MCP events through the real RV2C AUTO
event loop. The fixture commits its counter and then terminates before replying.

PASS criteria:
- the fixture counter becomes exactly 1;
- RV2C does not request a native -> STDIO switch/replay;
- the counter remains 1 through a short post-failure grace period.

The production AUTO policy intentionally may keep the native Realtime session
alive after a completed MCP call reports an ambiguous HTTP error. Session-stop
policy is separate from the safety invariant being tested here; this probe proves
that the already-executed mutation is not replayed automatically.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import sys
import time

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import rv1_realtime_audio as rv1
from scripts.rv2_auto_mcp import AutoState, event_loop, wait_native_ready_and_discovery
from scripts.rv2c_fault_mcp_server import read_state
from voice_assistant.realtime.engine import RealtimeEngineConfig, RealtimeMCPServer
from voice_assistant.realtime.openai_realtime import OpenAIRealtimeEngine

DEFAULT_URL = "https://raspberrypi-1.tail70348.ts.net/rv2c/mcp"
INSTRUCTIONS = """You are executing one deterministic RV2C safety probe.
Do not speak before using the MCP tool.
Call the MCP tool mutate_then_disconnect exactly once with token rv2c-native-postdispatch.
Never retry the tool, even if it errors or the MCP server disconnects.
Do not call read_counter.
After the tool attempt, do nothing else.
"""


async def wait_for_committed_once(*, timeout: float, switch_event: asyncio.Event) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = read_state()
        count = int(state.get("count") or 0)
        if count > 1:
            raise AssertionError(f"fixture mutation was replayed; count={count}")
        if switch_event.is_set():
            raise AssertionError("unsafe native -> STDIO fallback/replay was requested after ambiguous mutation")
        if count == 1:
            return state
        await asyncio.sleep(0.1)
    raise RuntimeError("timed out waiting for fixture mutation to commit")


async def run(args) -> int:
    env_file = Path(args.env_file).resolve()
    load_dotenv(env_file, override=True)
    api_key = rv1.read_secret("OPENAI_API_KEY", env_file)
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY / OPENAI_API_KEY_FILE is not configured")

    before = read_state()
    if int(before.get("count") or 0) != 0:
        raise RuntimeError(f"fixture counter must be reset to 0 before probe, got {before}")

    server = RealtimeMCPServer(
        label="rv2c-fault",
        url=args.mcp_url,
        allowed_tools=("mutate_then_disconnect",),
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

    state = AutoState(last_user_text="execute rv2c native post-dispatch probe")
    playback_queue: asyncio.Queue = asyncio.Queue()
    interrupted_responses: set[str] = set()
    first_audio_played: dict[str, float] = {}
    session_stop = asyncio.Event()
    global_stop = asyncio.Event()
    switch_event = asyncio.Event()
    loop_task = None

    try:
        await engine.start()
        ready, discovery = await wait_native_ready_and_discovery(engine, state, timeout=args.discovery_timeout)
        print(f"RV2C_NATIVE fixture discovery: ready={ready} discovery={discovery}", flush=True)
        if not ready or discovery != "completed":
            raise RuntimeError("provider-native fixture discovery did not complete")

        loop_task = asyncio.create_task(
            event_loop(
                engine,
                state,
                "native",
                None,
                playback_queue,
                interrupted_responses,
                first_audio_played,
                session_stop,
                global_stop,
                switch_event,
            ),
            name="rv2c-native-postdispatch-events",
        )

        await engine.send_text(
            "Call mutate_then_disconnect exactly once with token rv2c-native-postdispatch. Do not retry it."
        )

        committed = await wait_for_committed_once(timeout=args.timeout, switch_event=switch_event)
        print("RV2C_NATIVE mutation committed once; observing no-replay grace period", flush=True)
        await asyncio.sleep(args.grace_period)

        after = read_state()
        count = int(after.get("count") or 0)
        print(
            f"RV2C_NATIVE result count={count} switch={str(switch_event.is_set()).lower()} "
            f"global_stop={str(global_stop.is_set()).lower()} committed={committed} state={after}",
            flush=True,
        )

        if count != 1:
            raise AssertionError(f"fixture mutation count must remain exactly 1, got {count}")
        if switch_event.is_set():
            raise AssertionError("unsafe native -> STDIO fallback/replay was requested after ambiguous mutation")

        print("RV2C provider-native post-dispatch probe OK: mutation committed once and was not replayed.")
        return 0
    finally:
        session_stop.set()
        global_stop.set()
        if loop_task is not None:
            loop_task.cancel()
            await asyncio.gather(loop_task, return_exceptions=True)
        await engine.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env.online")
    parser.add_argument("--mcp-url", default=os.getenv("RV2C_FAULT_MCP_URL", DEFAULT_URL))
    parser.add_argument("--model", default=os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime-2.1"))
    parser.add_argument("--discovery-timeout", type=float, default=20.0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--grace-period", type=float, default=3.0)
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"RV2C_NATIVE failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
