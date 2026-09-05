"""Web monitor compatibility wrapper for RV2D realtime controls."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import threading
from typing import Any, Callable

try:
    from . import web_monitor_base as _base
    from .mcp_realtime_web_endpoint import save_mcp_realtime_policy_from_snapshot
except ImportError:  # pragma: no cover - direct script fallback
    import web_monitor_base as _base
    from mcp_realtime_web_endpoint import save_mcp_realtime_policy_from_snapshot

for _name in dir(_base):
    if not _name.startswith("_") and _name != "WebMonitor":
        globals()[_name] = getattr(_base, _name)

_BaseWebMonitor = _base.WebMonitor
_START_PATCH_LOCK = threading.Lock()
VOICE_ENGINE_ONLINE = {"classic", "openai-realtime"}
VOICE_ENGINE_OFFLINE = {"local"}


def _active_env_file_from_snapshot(snapshot: dict[str, Any]) -> Path:
    config = snapshot.get("config") or {}
    env_values = config.get("env") or {}
    mode = str(env_values.get("CONNECTIVITY_MODE") or "").strip().lower()
    env_dir = Path(os.getenv("ASSISTANT_AUTO_ENV_DIR", "."))
    if mode == "offline":
        return env_dir / ".env.offline"
    return env_dir / ".env.online"


def _write_env_value(path: Path, key: str, value: str) -> None:
    if not path.is_file():
        raise ValueError(f"active env file not found: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    prefix = f"{key}="
    replaced = False
    output: list[str] = []
    for line in lines:
        if line.startswith(prefix):
            if not replaced:
                output.append(f"{key}={value}")
                replaced = True
            continue
        output.append(line)
    if not replaced:
        insert_at = 0
        for index, line in enumerate(output):
            if line.startswith("CONNECTIVITY_MODE="):
                insert_at = index + 1
                break
        output.insert(insert_at, f"{key}={value}")
    text = "\n".join(output) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        tmp_path = Path(handle.name)
    tmp_path.replace(path)


class WebMonitor(_BaseWebMonitor):
    """Historical WebMonitor plus RV2D realtime policy and voice-engine routes."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._mcp_realtime_policy_save_handler: Callable[[str, dict[str, Any]], dict[str, Any]] = self._save_mcp_realtime_policy

    def set_mcp_realtime_policy_save_handler(self, handler: Callable[[str, dict[str, Any]], dict[str, Any]]) -> None:
        with self._lock:
            self._mcp_realtime_policy_save_handler = handler

    def _save_mcp_realtime_policy(self, server_name: str, policy: dict[str, Any]) -> dict[str, Any]:
        safe_policy, refreshed_config = save_mcp_realtime_policy_from_snapshot(self.snapshot(), server_name, policy)
        self.update(mcp_config=refreshed_config)
        return {"ok": True, "server": server_name, "policy": safe_policy}

    def _save_voice_engine(self, engine: str) -> dict[str, Any]:
        snapshot = self.snapshot()
        config = snapshot.get("config") or {}
        env_values = dict(config.get("env") or {})
        connectivity = str(env_values.get("CONNECTIVITY_MODE") or "online").strip().lower()
        normalized = str(engine or "").strip().lower()
        allowed = VOICE_ENGINE_OFFLINE if connectivity == "offline" else VOICE_ENGINE_ONLINE
        if normalized not in allowed:
            expected = ", ".join(sorted(allowed))
            raise ValueError(f"voice_engine must be one of: {expected}")
        env_file = _active_env_file_from_snapshot(snapshot)
        _write_env_value(env_file, "VOICE_ENGINE", normalized)
        env_values["VOICE_ENGINE"] = normalized
        self.update(env_values=env_values)
        return {
            "ok": True,
            "voice_engine": normalized,
            "connectivity_mode": connectivity,
            "restart_required": True,
            "message": "Voice engine saved. Restart LiveStageAssistant to apply it.",
        }

    def start(self, host: str = "127.0.0.1", port: int = 8765) -> tuple[str, int]:
        monitor = self
        with _START_PATCH_LOCK:
            original_http_server = _base.ThreadingHTTPServer

            def server_factory(server_address, handler_class):
                class RealtimePolicyHandler(handler_class):
                    def do_POST(self) -> None:
                        parsed = _base.urlparse(self.path)
                        if parsed.path not in {"/api/mcp-realtime-policy", "/api/voice-engine"}:
                            super().do_POST()
                            return
                        if self._auth_required(parsed.path):
                            self._send_auth_required()
                            return
                        if parsed.path == "/api/voice-engine":
                            self._handle_voice_engine_save()
                            return
                        self._handle_mcp_realtime_policy_save()

                    def _handle_voice_engine_save(self) -> None:
                        payload = self._read_json_body(max_bytes=16 * 1024)
                        if payload is None:
                            return
                        try:
                            result = monitor._save_voice_engine(str(payload.get("voice_engine") or ""))
                        except ValueError as error:
                            self._send_json_error(400, {"ok": False, "error": {"message": str(error)}})
                            return
                        except Exception as error:  # pragma: no cover
                            self._send_json_error(500, {"ok": False, "error": {"message": f"Could not save voice engine: {error}"}})
                            return
                        self._send_json(result)

                    def _handle_mcp_realtime_policy_save(self) -> None:
                        payload = self._read_json_body(max_bytes=64 * 1024)
                        if payload is None:
                            return
                        server_name = str(payload.get("server") or "").strip()
                        policy = payload.get("policy")
                        if not server_name:
                            self._send_json_error(400, {"ok": False, "error": {"message": "server is required"}})
                            return
                        if not isinstance(policy, dict):
                            self._send_json_error(400, {"ok": False, "error": {"message": "policy must be an object"}})
                            return
                        handler = monitor._mcp_realtime_policy_save_handler
                        if handler is None:
                            self.send_error(503, "MCP realtime policy save is not available")
                            return
                        try:
                            result = handler(server_name, policy)
                        except ValueError as error:
                            self._send_json_error(400, {"ok": False, "error": {"message": str(error)}})
                            return
                        except Exception as error:  # pragma: no cover
                            self._send_json_error(500, {"ok": False, "error": {"message": f"Could not save MCP realtime policy: {error}"}})
                            return
                        self._send_json(result)

                return original_http_server(server_address, RealtimePolicyHandler)

            _base.ThreadingHTTPServer = server_factory
            try:
                return super().start(host, port)
            finally:
                _base.ThreadingHTTPServer = original_http_server
