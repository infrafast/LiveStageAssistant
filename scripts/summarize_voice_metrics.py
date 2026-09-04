#!/usr/bin/env python3
"""Summarize classic/realtime VOICE_METRICS log lines for RV benchmarks."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voice_assistant.voice_metrics import parse_voice_metrics_line

STT_RE = re.compile(r"STT finished in ([0-9.]+)s\.")


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * p
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    fraction = position - low
    return ordered[low] + (ordered[high] - ordered[low]) * fraction


def summarize(values: list[float]) -> str:
    if not values:
        return "n=0"
    return (
        f"n={len(values)} mean={statistics.fmean(values):.0f}ms "
        f"p50={percentile(values, 0.50):.0f}ms "
        f"p90={percentile(values, 0.90):.0f}ms "
        f"p95={percentile(values, 0.95):.0f}ms"
    )


def collect(lines: list[str]) -> dict[str, list[float]]:
    samples = {"stt_ms": [], "agent_ms": [], "tts_ms": [], "turn_ms": []}
    for line in lines:
        stt_match = STT_RE.search(line)
        if stt_match:
            samples["stt_ms"].append(float(stt_match.group(1)) * 1000.0)
        record = parse_voice_metrics_line(line)
        if not record:
            continue
        durations = record.get("durations_ms")
        if not isinstance(durations, dict):
            continue
        for key in ("agent_ms", "tts_ms", "turn_ms"):
            value = durations.get(key)
            if isinstance(value, (int, float)):
                samples[key].append(float(value))
    return samples


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("logfile", nargs="?", help="journal/log file; stdin when omitted")
    args = parser.parse_args()
    lines = (
        Path(args.logfile).read_text(errors="replace").splitlines()
        if args.logfile
        else sys.stdin.read().splitlines()
    )
    samples = collect(lines)
    for key in ("stt_ms", "agent_ms", "tts_ms", "turn_ms"):
        print(f"{key}: {summarize(samples[key])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
