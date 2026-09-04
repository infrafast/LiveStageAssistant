#!/usr/bin/env python3
"""Summarize RV1_METRICS lines from an isolated realtime audio run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys

PREFIX = "RV1_METRICS "


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * p
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    fraction = rank - low
    return ordered[low] * (1.0 - fraction) + ordered[high] * fraction


def parse_lines(lines: list[str]) -> list[dict]:
    records = []
    for line in lines:
        pos = line.find(PREFIX)
        if pos < 0:
            continue
        try:
            value = json.loads(line[pos + len(PREFIX) :].strip())
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def format_metric(name: str, records: list[dict]) -> None:
    values = [float(record[name]) for record in records if isinstance(record.get(name), (int, float))]
    if not values:
        print(f"{name}: n=0")
        return
    print(
        f"{name}: n={len(values)} mean={statistics.fmean(values):.0f}ms "
        f"p50={percentile(values, 0.50):.0f}ms p90={percentile(values, 0.90):.0f}ms "
        f"p95={percentile(values, 0.95):.0f}ms"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("logfile", nargs="?", help="RV1 output log; stdin when omitted")
    args = parser.parse_args()
    lines = Path(args.logfile).read_text(errors="replace").splitlines() if args.logfile else sys.stdin.read().splitlines()
    records = parse_lines(lines)
    if not records:
        print("No RV1_METRICS records found.")
        return 1

    completed = [record for record in records if record.get("status") == "completed" and not record.get("interrupted")]
    interrupted = [record for record in records if record.get("interrupted")]
    print(f"turns: total={len(records)} completed={len(completed)} interrupted={len(interrupted)}")
    models = sorted({str(record.get("model") or "") for record in records if record.get("model")})
    print(f"models: {', '.join(models)}")
    format_metric("speech_end_to_first_audio_ms", completed)
    format_metric("speech_end_to_first_playback_ms", completed)
    format_metric("speech_end_to_response_done_ms", completed)

    costs = [float(record["cost_usd"]) for record in records if isinstance(record.get("cost_usd"), (int, float))]
    if costs:
        total = sum(costs)
        print(
            f"cost: n={len(costs)} total_usd=${total:.6f} avg_usd=${statistics.fmean(costs):.6f} "
            f"per_100≈${statistics.fmean(costs) * 100:.4f}"
        )
    else:
        print("cost: n=0 (model pricing unknown or usage unavailable)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
