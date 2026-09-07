"""Provider- and MCP-neutral runtime status contract for LSA observability."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import tempfile
from typing import Any


@dataclass(frozen=True)
class MCPRuntimeStatus:
    name: str
    configured_transport: str
    effective_transport: str
    permission: str
    healthy: bool | None = None
    detail: str = ""


@dataclass(frozen=True)
class RuntimeStatus:
    connectivity: str
    engine: str
    provider: str = ""
    model: str = ""
    voice: str = ""
    ready: bool = False
    profile: str = ""
    mcp: tuple[MCPRuntimeStatus, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def status_json(status: RuntimeStatus) -> str:
    return json.dumps(status.as_dict(), ensure_ascii=False, separators=(",", ":"))


def write_status_file(path: Path, status: RuntimeStatus) -> None:
    """Atomically replace a JSON status file for WebMonitor/health consumers."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = status_json(status) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        handle.write(payload)
        tmp = Path(handle.name)
    tmp.replace(path)


def read_status_file(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
