"""Provider- and MCP-neutral runtime status contract for LSA observability."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping


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
    semantic_state: str = ""
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


def resolve_config_path(value: str, *, profile: Path, root: Path) -> Path:
    path = Path(os.path.expandvars(str(value or "mcp_servers.json"))).expanduser()
    if path.is_absolute():
        return path
    candidates = (profile.parent / path, root / path)
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[-1])


def configured_mcp_statuses(values: Mapping[str, object], *, profile: Path, root: Path) -> tuple[MCPRuntimeStatus, ...]:
    """Read generic per-MCP transport/permission config without domain assumptions."""
    config_path = resolve_config_path(str(values.get("MCP_CONFIG") or "mcp_servers.json"), profile=profile, root=root)
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return ()
    servers = raw.get("mcpServers") if isinstance(raw, dict) else None
    if not isinstance(servers, dict):
        return ()
    result: list[MCPRuntimeStatus] = []
    for name, entry in servers.items():
        if not isinstance(entry, dict):
            continue
        realtime = entry.get("realtime") if isinstance(entry.get("realtime"), dict) else {}
        configured = str(realtime.get("transport") or "stdio").strip().lower()
        permissions = realtime.get("permissions") if isinstance(realtime.get("permissions"), dict) else {}
        permission = str(permissions.get("mode") or "open").strip().lower()
        effective = configured if configured in {"stdio", "native"} else ""
        result.append(MCPRuntimeStatus(
            name=str(name),
            configured_transport=configured,
            effective_transport=effective,
            permission=permission,
            healthy=None,
            detail="configured",
        ))
    return tuple(result)


class RuntimeStatusTracker:
    """Small immutable-state tracker shared by the runtime and future monitors."""

    def __init__(self, path: Path, status: RuntimeStatus) -> None:
        self.path = Path(path)
        self.status = status
        self.flush()

    def flush(self) -> None:
        write_status_file(self.path, self.status)

    def set_runtime(self, **changes: Any) -> None:
        self.status = replace(self.status, **changes)
        self.flush()

    def set_mcp(
        self,
        name: str,
        *,
        effective_transport: str | None = None,
        healthy: bool | None = None,
        detail: str | None = None,
    ) -> None:
        items = list(self.status.mcp)
        for index, item in enumerate(items):
            if item.name != name:
                continue
            items[index] = replace(
                item,
                effective_transport=item.effective_transport if effective_transport is None else effective_transport,
                healthy=item.healthy if healthy is None else healthy,
                detail=item.detail if detail is None else detail,
            )
            self.status = replace(self.status, mcp=tuple(items))
            self.flush()
            return
