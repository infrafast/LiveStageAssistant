#!/usr/bin/env python3
"""Exercise RV2C fallback safety through the real event loop without stage writes.

This probe feeds synthetic native MCP events into scripts.rv2_auto_mcp.event_loop:
- read-only call + connection loss must request STDIO fallback/replay;
- write call + connection loss must suppress fallback and stop globally;
- unknown call metadata + connection loss must suppress fallback and stop globally.

No provider, MCP server, mixer or QLC+ endpoint is contacted.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.rv2_auto_mcp import AutoState, event_loop


@dataclass
class ProbeResult:
    name: str
    switch_requested: bool
    global_stop: bool
    replay_text: str


class FakeEngine:
    def __init__(self, events):
        self._events = asyncio.Queue()
        for event in events:
            self._events.put_nowait(event)
        self.config = SimpleNamespace(model="rv2c-probe")

    async def next_event(self):
        return await self._events.get()


def event(event_type: str, **data):
    return SimpleNamespace(type=event_type, data=data)


async def run_case(name: str, *, metadata, expected_switch: bool, expected_global_stop: bool) -> ProbeResult:
    tool_name = "probe_tool"
    state = AutoState(last_user_text="commande de validation")
    if metadata is not None:
        state.native_tools[tool_name] = metadata

    engine = FakeEngine(
        [
            event(
                "mcp_call",
                phase="added",
                item={"name": tool_name, "arguments": '{"value":1}'},
            ),
            event("connection_error", error="synthetic post-dispatch connection loss"),
        ]
    )

    playback_queue = asyncio.Queue()
    interrupted_responses = set()
    first_audio_played = {}
    session_stop = asyncio.Event()
    global_stop = asyncio.Event()
    switch_event = asyncio.Event()

    await asyncio.wait_for(
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
        timeout=2.0,
    )

    result = ProbeResult(
        name=name,
        switch_requested=switch_event.is_set(),
        global_stop=global_stop.is_set(),
        replay_text=state.replay_text,
    )
    print(
        f"RV2C_EVENT {name} switch={str(result.switch_requested).lower()} "
        f"global_stop={str(result.global_stop).lower()} replay={result.replay_text!r}"
    )

    if result.switch_requested != expected_switch:
        raise AssertionError(
            f"{name}: switch_requested={result.switch_requested}, expected {expected_switch}"
        )
    if result.global_stop != expected_global_stop:
        raise AssertionError(
            f"{name}: global_stop={result.global_stop}, expected {expected_global_stop}"
        )

    if expected_switch and not result.replay_text:
        raise AssertionError(f"{name}: expected original user text to be queued for safe replay")
    if not expected_switch and result.replay_text:
        raise AssertionError(f"{name}: unsafe replay text was queued: {result.replay_text!r}")

    return result


async def main_async() -> None:
    await run_case(
        "read_only_connection_loss",
        metadata={"annotations": {"readOnlyHint": True}},
        expected_switch=True,
        expected_global_stop=False,
    )
    await run_case(
        "write_connection_loss",
        metadata={"annotations": {"readOnlyHint": False}},
        expected_switch=False,
        expected_global_stop=True,
    )
    await run_case(
        "unknown_connection_loss",
        metadata={},
        expected_switch=False,
        expected_global_stop=True,
    )
    print("RV2C event-path fault probe OK: ambiguous post-dispatch writes are never replayed.")


def main() -> int:
    asyncio.run(main_async())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
