#!/usr/bin/env python3
"""Safe RV2D write-path probe using a temporary copy of the active MCP config."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voice_assistant.realtime.mcp_config import load_mcp_inventory, server_summary, update_mcp_realtime_policy


def resolve_path(value: str, env_file: Path) -> Path:
    path = Path(os.path.expandvars(value)).expanduser()
    if path.is_absolute():
        return path
    candidates = [env_file.parent / path, ROOT / path]
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[-1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env.online")
    parser.add_argument("--mcp-config", default="")
    parser.add_argument("--mcp-server", default="mixer")
    args = parser.parse_args()

    env_file = Path(args.env_file).resolve()
    load_dotenv(env_file, override=True)
    config_value = (args.mcp_config or os.getenv("MCP_CONFIG") or "mcp_servers.json").strip()
    source = resolve_path(config_value, env_file).resolve()
    source_before = source.read_bytes()
    inventory = load_mcp_inventory(source)
    if args.mcp_server not in inventory:
        raise RuntimeError(f"MCP server {args.mcp_server!r} not found in {source}")
    current = inventory[args.mcp_server]

    with tempfile.TemporaryDirectory(prefix="rv2d-config-probe-") as tmp:
        probe = Path(tmp) / source.name
        shutil.copy2(source, probe)
        update_mcp_realtime_policy(
            probe,
            args.mcp_server,
            transport=current.realtime.transport,
            permission_mode=current.realtime.permissions.mode,
            allowed_tools=list(current.realtime.permissions.allowed_tools),
            native_url=current.native.url,
            native_headers=current.native.headers,
        )
        updated = load_mcp_inventory(probe)[args.mcp_server]
        payload = json.loads(probe.read_text(encoding="utf-8"))
        print(f"RV2D write probe source: {source}")
        print("RV2D write probe server " + json.dumps(server_summary(updated), ensure_ascii=False, separators=(",", ":")))
        print(f"RV2D write probe servers preserved: {len(payload.get('mcpServers') or {})}")

    if source.read_bytes() != source_before:
        raise RuntimeError("source MCP config changed during temporary write probe")
    print("RV2D config write probe: OK (temporary copy updated; source unchanged)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
