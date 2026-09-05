#!/usr/bin/env python3
"""Run RV2C AUTO using only the canonical RV2D MCP inventory policy."""

from __future__ import annotations

import argparse
import asyncio
from argparse import Namespace
import os
from pathlib import Path
import sys

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import rv2_auto_mcp
from voice_assistant.realtime.mcp_config import load_mcp_inventory


def resolve_path(value: str, env_file: Path) -> Path:
    path = Path(os.path.expandvars(value)).expanduser()
    if path.is_absolute():
        return path
    candidates = [env_file.parent / path, ROOT / path]
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[-1])


def build_auto_args(cli) -> Namespace:
    env_file = Path(cli.env_file).resolve()
    load_dotenv(env_file, override=True)
    config_value = (cli.mcp_config or os.getenv("MCP_CONFIG") or "mcp_servers.json").strip()
    config_path = resolve_path(config_value, env_file)
    inventory = load_mcp_inventory(config_path)
    if cli.mcp_server not in inventory:
        raise RuntimeError(f"MCP server {cli.mcp_server!r} not found in {config_path}")
    server = inventory[cli.mcp_server]
    if server.realtime.transport != "auto":
        raise RuntimeError(
            f"MCP server {cli.mcp_server!r} is configured for realtime transport "
            f"{server.realtime.transport!r}, not 'auto'"
        )
    if not server.native.url:
        raise RuntimeError(f"MCP server {cli.mcp_server!r} has no native HTTPS URL configured")

    policy = server.realtime.permissions
    headers = [f"{key}={value}" for key, value in server.native.headers.items()]
    return Namespace(
        env_file=str(env_file),
        model=cli.model,
        voice=cli.voice,
        duration=cli.duration,
        mcp_config=str(config_path),
        mcp_server=cli.mcp_server,
        mcp_label=cli.mcp_label,
        mcp_url=server.native.url,
        mcp_header=headers,
        mcp_authorization_env="",
        permission_mode=policy.mode,
        allow_tool=list(policy.allowed_tools),
        discover_only=False,
        input_device=cli.input_device,
        output_device=cli.output_device,
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--env-file", default=".env.online")
    result.add_argument("--mcp-config", default="")
    result.add_argument("--mcp-server", default="mixer")
    result.add_argument("--mcp-label", default="")
    result.add_argument("--model", default="gpt-realtime-2.1")
    result.add_argument("--voice", default="marin")
    result.add_argument("--duration", type=float, default=300.0)
    result.add_argument("--input-device", default=None)
    result.add_argument("--output-device", default=None)
    return result


async def run(cli) -> int:
    args = build_auto_args(cli)
    print(
        f"RV2D AUTO config policy: server={args.mcp_server} transport=auto "
        f"permission={args.permission_mode} native_url={args.mcp_url}",
        flush=True,
    )
    return await rv2_auto_mcp.run(args)


def main() -> int:
    cli = parser().parse_args()
    try:
        return asyncio.run(run(cli))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"RV2D failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
