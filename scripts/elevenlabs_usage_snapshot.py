#!/usr/bin/env python3
"""Capture ElevenLabs subscription usage for RV0 before/after cost measurements."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
import urllib.request


def read_key(path: Path) -> str:
    key = path.read_text(encoding="utf-8").strip()
    if not key:
        raise RuntimeError(f"empty ElevenLabs API key file: {path}")
    return key


def fetch_subscription(api_key: str) -> dict:
    request = urllib.request.Request(
        "https://api.elevenlabs.io/v1/user/subscription",
        headers={"xi-api-key": api_key},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--key-file", default="ELEVENLABS_API_KEY.txt")
    parser.add_argument("--output", help="optional JSON snapshot file")
    args = parser.parse_args()

    payload = fetch_subscription(read_key(Path(args.key_file)))
    snapshot = {
        "timestamp": int(time.time()),
        "tier": str(payload.get("tier") or "unknown"),
        "status": str(payload.get("status") or "unknown"),
        "character_count": int(payload.get("character_count") or 0),
        "character_limit": int(payload.get("character_limit") or 0),
        "current_overage": payload.get("current_overage") or {},
    }
    text = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    print(text)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
