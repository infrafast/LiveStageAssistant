#!/usr/bin/env python3
"""Static audit for XMSeries-MCP OSC execution-feedback safety.

This script is intentionally read-only. It inspects a local XMSeries-MCP checkout and
checks the contracts LiveStageAssistant relies on for stage-safe writes:

- immediate channel fader writes use writeLevelAndVerify/writeAndVerify;
- immediate mute/send writes use verified writes;
- transactional writes re-read the same OSC address and fail on disconnect;
- ramp automation verifies its final value;
- raw delayed OSC remains explicitly detectable as a non-transactional exception.

Exit 0 means the expected verified paths are present. Known non-transactional exceptions
are reported as WARN and do not fail the audit until the server contract is hardened.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


REQUIRED_OSC_CLIENT_SNIPPETS = {
    "transactional write entry": "async writeAndVerify(address: string, args: any[]",
    "same-address readback": "actual = await this.sendAndReceive(address)",
    "disconnect on failed readback": "impossible de confirmer l'ecriture OSC",
    "channel fader verified": "await this.writeLevelAndVerify(path, level, { label: `channel ${channel} fader` });",
    "channel mute verified": "await this.writeAndVerify(path, [mute ? 0 : 1]",
    "channel send verified": "await this.writeLevelAndVerify(path, level, { label: `channel ${channel} send to bus ${bus}` });",
}

REQUIRED_AUTOMATION_SNIPPETS = {
    "ramp final verification": "await this.verifyRampFinalValue(job, action, clamp01(to));",
    "ramp final readback": "actual = await action.read();",
}

KNOWN_EXCEPTION_SNIPPETS = {
    "delayed raw OSC preflight + unverified send": (
        "await osc.assertMixerOnline();",
        "await osc.sendRaw(command.address, args, { allowOfflineWrite: true });",
    ),
    "raw ramp intermediate unchecked send": (
        "write: (level) => osc.sendRaw(writeAddress, [coerceOscArg(level, target.osctype || \"float\")], { allowOfflineWrite: true })",
    ),
    "bulk includeMain unchecked fader": "await osc.setFaderUnchecked(channel, converted.level);",
}


def check_contains(text: str, label: str, snippet: str) -> bool:
    ok = snippet in text
    print(f"RV2C_OSC_FEEDBACK {'OK' if ok else 'FAIL'} {label}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xmseries-dir", default="/home/pi/XMSeries-MCP")
    args = parser.parse_args()

    root = Path(args.xmseries_dir).resolve()
    osc_client_path = root / "src" / "osc-client.ts"
    automation_path = root / "src" / "automation.ts"
    index_path = root / "src" / "index.ts"

    missing_files = [p for p in (osc_client_path, automation_path, index_path) if not p.is_file()]
    if missing_files:
        for path in missing_files:
            print(f"RV2C_OSC_FEEDBACK FAIL missing {path}")
        return 2

    osc_client = osc_client_path.read_text(encoding="utf-8")
    automation = automation_path.read_text(encoding="utf-8")
    index = index_path.read_text(encoding="utf-8")

    ok = True
    for label, snippet in REQUIRED_OSC_CLIENT_SNIPPETS.items():
        ok = check_contains(osc_client, label, snippet) and ok
    for label, snippet in REQUIRED_AUTOMATION_SNIPPETS.items():
        ok = check_contains(automation, label, snippet) and ok

    for label, needle in KNOWN_EXCEPTION_SNIPPETS.items():
        if isinstance(needle, tuple):
            present = all(part in index for part in needle)
        else:
            present = needle in index
        print(f"RV2C_OSC_FEEDBACK {'WARN' if present else 'OK'} {label}{' still present' if present else ' not detected'}")

    if not ok:
        print("RV2C OSC feedback audit FAILED: a required transactional/readback contract is missing.")
        return 1

    print("RV2C OSC feedback audit OK: core immediate writes and ramp final state are verified; WARN lines are known exceptions to harden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
