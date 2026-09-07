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


def main() -> int:
    benchmark.capture_once = capture_vad_utterance
    if "--env-file" not in sys.argv:
        sys.argv.extend(["--env-file", DEFAULT_SERVICE_ENV])
    return benchmark.main()


if __name__ == "__main__":
    raise SystemExit(main())
