#!/usr/bin/env python3
"""Compare two ElevenLabs RV0 usage snapshots."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("before")
    parser.add_argument("after")
    parser.add_argument("--price-per-1k", type=float, default=0.10,
                        help="ElevenLabs API TTS USD per 1K characters; default 0.10 for v2/v3")
    args = parser.parse_args()

    before = load(args.before)
    after = load(args.after)
    delta = int(after.get("character_count") or 0) - int(before.get("character_count") or 0)
    if delta < 0:
        raise SystemExit("character_count decreased; snapshots likely cross a billing reset")
    cost = delta / 1000.0 * args.price_per_1k
    elapsed = int(after.get("timestamp") or 0) - int(before.get("timestamp") or 0)
    print(f"elevenlabs: characters={delta} elapsed={elapsed}s estimated_api_usd=${cost:.6f} price_per_1k=${args.price_per_1k:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
