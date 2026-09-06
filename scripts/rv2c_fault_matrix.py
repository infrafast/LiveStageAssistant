#!/usr/bin/env python3
"""Print and verify the RV2C native->STDIO fallback safety matrix.

This probe is intentionally deterministic and does not contact a provider or an
MCP server. It validates the policy that decides whether a logical request may
be replayed after a native transport failure.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voice_assistant.realtime.mcp_auto import fault_matrix


EXPECTED = {
    "auth_before_dispatch": True,
    "timeout_before_dispatch": True,
    "connection_before_dispatch": True,
    "timeout_after_read_dispatch": True,
    "timeout_after_write_dispatch": False,
    "connection_after_write_dispatch": False,
    "timeout_after_unknown_dispatch": False,
    "explicit_not_executed_write": True,
}


def main() -> int:
    failures: list[str] = []
    for name, decision in fault_matrix():
        expected = EXPECTED[name]
        record = {
            "case": name,
            "fallback": decision.fallback,
            "expected": expected,
            "classification": decision.classification,
            "failure_kind": decision.failure_kind,
            "dispatched": decision.dispatched,
            "read_only": decision.read_only,
            "reason": decision.reason,
        }
        print("RV2C_FAULT " + json.dumps(record, ensure_ascii=False, separators=(",", ":")), flush=True)
        if decision.fallback != expected:
            failures.append(name)

    if failures:
        print("RV2C fault matrix FAILED: " + ", ".join(failures), file=sys.stderr, flush=True)
        return 1

    print("RV2C fault matrix OK: ambiguous post-dispatch writes are never replayed automatically.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
