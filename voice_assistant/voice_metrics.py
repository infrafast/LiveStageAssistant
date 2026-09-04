"""Provider-neutral voice-turn timing primitives for classic/realtime benchmarks."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import time
from typing import Callable

VOICE_METRICS_PREFIX = "VOICE_METRICS "


@dataclass
class VoiceTurnMetrics:
    """Capture monotonic event timestamps without changing runtime control flow."""

    pipeline: str
    clock: Callable[[], float] = time.perf_counter
    started_at: float = field(init=False)
    events: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.started_at = self.clock()

    def mark(self, event: str) -> float:
        if not event:
            raise ValueError("event name is required")
        elapsed_ms = (self.clock() - self.started_at) * 1000.0
        self.events[event] = elapsed_ms
        return elapsed_ms

    def duration_ms(self, start_event: str, end_event: str) -> float | None:
        start = self.events.get(start_event)
        end = self.events.get(end_event)
        if start is None or end is None or end < start:
            return None
        return end - start

    def record(self) -> dict[str, object]:
        durations = {
            "agent_ms": self.duration_ms("command_accepted", "agent_response_ready"),
            "tts_ms": self.duration_ms("tts_start", "tts_end"),
            "turn_ms": self.duration_ms("command_accepted", "tts_end"),
        }
        return {
            "schema": 1,
            "pipeline": self.pipeline,
            "events_ms": {key: round(value, 3) for key, value in self.events.items()},
            "durations_ms": {
                key: (round(value, 3) if value is not None else None)
                for key, value in durations.items()
            },
        }

    def to_log_line(self) -> str:
        return VOICE_METRICS_PREFIX + json.dumps(self.record(), separators=(",", ":"), sort_keys=True)


def parse_voice_metrics_line(line: str) -> dict[str, object] | None:
    marker = line.find(VOICE_METRICS_PREFIX)
    if marker < 0:
        return None
    payload = line[marker + len(VOICE_METRICS_PREFIX) :].strip()
    if not payload:
        return None
    try:
        value = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
