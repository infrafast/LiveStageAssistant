#!/usr/bin/env python3
"""Functional, read-only probe for Realtime MCP AUTO transport selection.

This probe performs MCP discovery only. It never executes a tool and does not
open audio devices. It mirrors the integrated Realtime service policy:
healthy local STDIO/bridge first, then provider-native HTTPS when local is not
available.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voice_assistant.realtime.mcp_config import load_mcp_inventory
from voice_assistant.realtime.service import native_server, probe_local_stdio, resolve_path


async def run(args) -> int:
    env_file = Path(args.env_file).resolve()
    load_dotenv(env_file, override=True)
    config_path = resolve_path(str(os.getenv("MCP_CONFIG") or "mcp_servers.json").strip(), env_file)
    raw_config = json.loads(config_path.read_text(encoding="utf-8"))
    inventory = load_mcp_inventory(config_path)

    found_auto = False
    failed = False
    for server in inventory.values():
        if server.realtime.transport != "auto":
            continue
        found_auto = True
        local_ok, local_reason = await probe_local_stdio(raw_config, server)
        if local_ok:
            result = {
                "server": server.name,
                "configured": "auto",
                "effective": "stdio",
                "reason": local_reason,
            }
            print("RV2C_AUTO_SELECTION " + json.dumps(result, ensure_ascii=False, separators=(",", ":")), flush=True)
            continue

        if server.native.url:
            try:
                native = await native_server(server, strict_probe=True)
                result = {
                    "server": server.name,
                    "configured": "auto",
                    "effective": "native",
                    "reason": local_reason,
                    "native_url": native.url,
                }
                print("RV2C_AUTO_SELECTION " + json.dumps(result, ensure_ascii=False, separators=(",", ":")), flush=True)
                continue
            except Exception as exc:
                native_error = str(exc)
        else:
            native_error = "no native URL configured"

        failed = True
        result = {
            "server": server.name,
            "configured": "auto",
            "effective": "unavailable",
            "local_reason": local_reason,
            "native_error": native_error,
        }
        print("RV2C_AUTO_SELECTION " + json.dumps(result, ensure_ascii=False, separators=(",", ":")), flush=True)

    if not found_auto:
        print("RV2C_AUTO_SELECTION no AUTO MCP servers configured", flush=True)
        return 0
    if failed:
        return 1
    print("RV2C AUTO selection probe OK", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env.online")
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"RV2C AUTO selection probe failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
