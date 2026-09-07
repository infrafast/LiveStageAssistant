#!/usr/bin/env python3
"""RV2C provider-native post-dispatch failure fixture.

This server is intentionally isolated from stage hardware. It exposes a tiny
Streamable HTTP MCP with one persistent counter mutation that deliberately
terminates the fixture process *after* committing the mutation and *before* a
tool result can be returned. That creates the exact ambiguous outcome RV2C
must never replay automatically across transports.

Typical local use:
    .venv/bin/python scripts/rv2c_fault_mcp_server.py reset
    .venv/bin/python scripts/rv2c_fault_mcp_server.py serve
    .venv/bin/python scripts/rv2c_fault_mcp_server.py show

The MCP endpoint is http://127.0.0.1:8799/mcp by default. For a provider-native
test, expose only this fixture endpoint through the same trusted HTTPS/Funnel
mechanism used for remote MCP. Never point the fixture at mixer/QLC endpoints.

When serving behind a reverse proxy/Funnel, set RV2C_FAULT_ALLOWED_HOSTS to the
public hostname (comma-separated if needed). Localhost/loopback remain allowed.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
import time

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

DEFAULT_HOST = os.getenv("RV2C_FAULT_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.getenv("RV2C_FAULT_PORT", "8799"))
COUNTER_PATH = Path(os.getenv("RV2C_FAULT_COUNTER", "/tmp/lsa-rv2c-fault-counter.json"))
EXTRA_ALLOWED_HOSTS = [
    value.strip()
    for value in os.getenv("RV2C_FAULT_ALLOWED_HOSTS", "").split(",")
    if value.strip()
]
ALLOWED_HOSTS = [
    "127.0.0.1:*",
    "localhost:*",
    "[::1]:*",
]
for allowed_host in EXTRA_ALLOWED_HOSTS:
    if allowed_host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(allowed_host)
    if ":" not in allowed_host and f"{allowed_host}:*" not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(f"{allowed_host}:*")


def read_state() -> dict:
    try:
        data = json.loads(COUNTER_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = {}
    return {
        "count": int(data.get("count") or 0),
        "last_token": str(data.get("last_token") or ""),
        "updated_at": float(data.get("updated_at") or 0.0),
    }


def write_state(state: dict) -> None:
    COUNTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=COUNTER_PATH.name + ".", dir=str(COUNTER_PATH.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, COUNTER_PATH)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def reset_state() -> dict:
    state = {"count": 0, "last_token": "", "updated_at": time.time()}
    write_state(state)
    return state


mcp = FastMCP(
    "LSA RV2C Fault Fixture",
    host=DEFAULT_HOST,
    port=DEFAULT_PORT,
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=ALLOWED_HOSTS,
    ),
)


@mcp.tool()
def read_counter() -> dict:
    """Read the RV2C fixture counter without changing it."""
    return read_state()


@mcp.tool()
def mutate_then_disconnect(token: str = "rv2c") -> dict:
    """Increment the fixture counter, persist it, then drop the server process.

    This intentionally creates an ambiguous post-dispatch mutation result. The
    caller must NOT automatically replay this mutation through another MCP
    transport merely because the connection disappears before a result arrives.
    """
    state = read_state()
    state["count"] = int(state.get("count") or 0) + 1
    state["last_token"] = str(token)
    state["updated_at"] = time.time()
    write_state(state)
    print(
        "RV2C_FAULT committed mutation "
        + json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        + "; terminating before response",
        flush=True,
    )
    os._exit(86)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve")
    sub.add_parser("reset")
    sub.add_parser("show")
    args = parser.parse_args()

    if args.command == "reset":
        print("RV2C_FAULT reset " + json.dumps(reset_state(), separators=(",", ":")))
        return 0
    if args.command == "show":
        print("RV2C_FAULT state " + json.dumps(read_state(), separators=(",", ":")))
        return 0

    print(
        f"RV2C_FAULT serving http://{DEFAULT_HOST}:{DEFAULT_PORT}/mcp "
        f"counter={COUNTER_PATH} allowed_hosts={ALLOWED_HOSTS}",
        flush=True,
    )
    mcp.run(transport="streamable-http")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
