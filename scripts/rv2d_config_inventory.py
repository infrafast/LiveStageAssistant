#!/usr/bin/env python3
"""Read-only RV2D MCP configuration inventory diagnostic."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voice_assistant.realtime.mcp_config import load_mcp_inventory, server_summary


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
    args = parser.parse_args()

    env_file = Path(args.env_file).resolve()
    load_dotenv(env_file, override=True)
    config_value = (args.mcp_config or os.getenv("MCP_CONFIG") or "mcp_servers.json").strip()
    config_path = resolve_path(config_value, env_file)
    inventory = load_mcp_inventory(config_path)

    print(f"RV2D MCP config: {config_path}")
    print(f"RV2D MCP servers: {len(inventory)}")
    for name in sorted(inventory):
        print("RV2D MCP server " + json.dumps(server_summary(inventory[name]), ensure_ascii=False, separators=(",", ":")))
    print("RV2D config inventory: OK (read-only; source file unchanged)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
