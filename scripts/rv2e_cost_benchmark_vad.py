#!/usr/bin/env python3
"""RV2E Classic-vs-Realtime cost benchmark using production-like Silero VAD capture."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import rv2e_classic_realtime_cost_benchmark as benchmark
from voice_assistant.benchmark_vad_capture import capture_vad_utterance


DEFAULT_SERVICE_ENV = "/etc/livestageassistant/.env.online"
BENCHMARK_QUERY = "Quel est le volume de vocal-clode ?"


def _validate_benchmark_transcript(text: str) -> None:
    normalized = text.casefold().replace("-", " ")
    has_volume = "volume" in normalized
    has_target = "vocal" in normalized and ("clode" in normalized or "claude" in normalized)
    if not (has_volume and has_target):
        raise RuntimeError(
            "recorded speech was not recognized as the fixed RV2E benchmark query; "
            f"got {text!r}. Expected a transcription of {BENCHMARK_QUERY!r}. No cost comparison produced."
        )


def main() -> int:
    benchmark.capture_once = capture_vad_utterance
    benchmark.DEFAULT_QUERY_HINT = BENCHMARK_QUERY
    benchmark.validate_fixed_query_transcript = _validate_benchmark_transcript
    if "--env-file" not in sys.argv:
        sys.argv.extend(["--env-file", DEFAULT_SERVICE_ENV])
    return benchmark.main()


if __name__ == "__main__":
    raise SystemExit(main())
