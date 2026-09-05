#!/usr/bin/env python3
"""Run one realtime MCP server from the canonical RV2D MCP inventory policy."""

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

from scripts import rv2_auto_mcp, rv2_native_mcp, rv2_stdio_mcp
from voice_assistant.realtime.mcp_config import CanonicalMCPServerConfig, load_mcp_inventory


def resolve_path(value: str, env_file: Path) -> Path:
    path = Path(os.path.expandvars(value)).expanduser()
    if path.is_absolute():
        return path
    candidates = [env_file.parent / path, ROOT / path]
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[-1])


def load_server_policy(cli) -> tuple[Path, CanonicalMCPServerConfig]:
    env_file = Path(cli.env_file).resolve()
    load_dotenv(env_file, override=True)
    config_value = (cli.mcp_config or os.getenv("MCP_CONFIG") or "mcp_servers.json").strip()
    config_path = resolve_path(config_value, env_file)
    inventory = load_mcp_inventory(config_path)
    server = inventory.get(cli.mcp_server)
    if server is None:
        raise RuntimeError(f"MCP server {cli.mcp_server!r} not found in {config_path}")
    return config_path, server


def _native_common(cli, config_path: Path, server: CanonicalMCPServerConfig) -> dict:
    if not server.native.url:
        raise RuntimeError(
            f"MCP server {server.name!r} is configured for {server.realtime.transport!r} "
            "but has no native HTTPS URL"
        )
    policy = server.realtime.permissions
    return {
        "env_file": str(Path(cli.env_file).resolve()),
        "model": cli.model,
        "voice": cli.voice,
        "duration": cli.duration,
        "mcp_config": str(config_path),
        "mcp_server": server.name,
        "mcp_label": cli.mcp_label,
        "mcp_url": server.native.url,
        "mcp_header": [f"{key}={value}" for key, value in server.native.headers.items()],
        "mcp_authorization_env": "",
        "permission_mode": policy.mode,
        "allow_tool": list(policy.allowed_tools),
        "discover_only": False,
        "input_device": cli.input_device,
        "output_device": cli.output_device,
    }


def build_runner_args(cli) -> tuple[str, Namespace]:
    config_path, server = load_server_policy(cli)
    transport = server.realtime.transport
    policy = server.realtime.permissions

    if transport == "stdio":
        if policy.mode == "approval":
            raise RuntimeError("STDIO approval mode is not yet implemented; use open or restricted")
        args = Namespace(
            env_file=str(Path(cli.env_file).resolve()),
            model=cli.model,
            voice=cli.voice,
            duration=cli.duration,
            mcp_config=str(config_path),
            mcp_server=server.name,
            allow_tool=list(policy.allowed_tools) if policy.mode == "restricted" else [],
            input_device=cli.input_device,
            output_device=cli.output_device,
        )
        return transport, args

    common = _native_common(cli, config_path, server)
    return transport, Namespace(**common)


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
    transport, args = build_runner_args(cli)
    print(
        f"RV2D realtime config policy: server={args.mcp_server} transport={transport} "
        f"permission={getattr(args, 'permission_mode', 'restricted' if args.allow_tool else 'open')}",
        flush=True,
    )
    if transport == "native":
        return await rv2_native_mcp.run(args)
    if transport == "stdio":
        return await rv2_stdio_mcp.run(args)
    if transport == "auto":
        return await rv2_auto_mcp.run(args)
    raise RuntimeError(f"unsupported realtime transport {transport!r}")


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
