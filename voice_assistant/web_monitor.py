"""Runtime-owned WebMonitor wrapper for common RV2D/RV8 controls."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any, Callable

try:
    from . import web_monitor_base as _base
    from .mcp_realtime_web_endpoint import save_mcp_realtime_policy_from_snapshot
    from .runtime_status import read_status_file
except ImportError:  # pragma: no cover - direct script fallback
    import web_monitor_base as _base
    from mcp_realtime_web_endpoint import save_mcp_realtime_policy_from_snapshot
    from runtime_status import read_status_file

for _name in dir(_base):
    if not _name.startswith("_") and _name != "WebMonitor":
        globals()[_name] = getattr(_base, _name)

_BaseWebMonitor = _base.WebMonitor
_START_PATCH_LOCK = threading.Lock()
VOICE_ENGINE_ONLINE = {"classic", "openai-realtime"}
VOICE_ENGINE_OFFLINE = {"local"}
DEFAULT_RUNTIME_STATUS_FILE = "/tmp/livestageassistant-runtime-status.json"
DEFAULT_RUNTIME_STATUS_STALE_SECONDS = 30.0
DEFAULT_REALTIME_MODEL = "gpt-realtime-2.1"
DEFAULT_REALTIME_VOICE = "marin"


def _active_env_file_from_snapshot(snapshot: dict[str, Any]) -> Path:
    config = snapshot.get("config") or {}
    env_values = config.get("env") or {}
    mode = str(env_values.get("CONNECTIVITY_MODE") or "").strip().lower()
    env_dir = Path(os.getenv("ASSISTANT_AUTO_ENV_DIR", "."))
    if mode == "offline":
        return env_dir / ".env.offline"
    return env_dir / ".env.online"


def _profile_env_files() -> tuple[Path, ...]:
    env_dir = Path(os.getenv("ASSISTANT_AUTO_ENV_DIR", "."))
    candidates = (env_dir / ".env.online", env_dir / ".env.offline")
    existing = tuple(path for path in candidates if path.is_file())
    return existing or candidates[:1]


def _runtime_status_file() -> Path:
    return Path(os.getenv("LSA_RUNTIME_STATUS_FILE", DEFAULT_RUNTIME_STATUS_FILE)).expanduser()


def _runtime_status_stale_seconds() -> float:
    try:
        return max(1.0, float(os.getenv("LSA_RUNTIME_STATUS_STALE_SECONDS", str(DEFAULT_RUNTIME_STATUS_STALE_SECONDS)) or DEFAULT_RUNTIME_STATUS_STALE_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_RUNTIME_STATUS_STALE_SECONDS


def _write_env_values(path: Path, values: dict[str, str]) -> None:
    """Atomically replace one or more env keys while preserving file mode/order."""
    if not path.is_file():
        raise ValueError(f"active env file not found: {path}")
    original_mode = path.stat().st_mode & 0o777
    requested = {str(key): str(value) for key, value in values.items()}
    remaining = dict(requested)
    lines = path.read_text(encoding="utf-8").splitlines()
    output: list[str] = []
    insert_at = 0
    for line in lines:
        if line.startswith("CONNECTIVITY_MODE="):
            insert_at = len(output) + 1
        key, sep, _value = line.partition("=")
        if sep and key in requested:
            if key in remaining:
                output.append(f"{key}={requested[key]}")
                remaining.pop(key, None)
            continue
        output.append(line)
    for key, value in remaining.items():
        output.insert(insert_at, f"{key}={value}")
        insert_at += 1
    text = "\n".join(output) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        tmp_path = Path(handle.name)
    os.chmod(tmp_path, original_mode)
    tmp_path.replace(path)


def _write_env_value(path: Path, key: str, value: str) -> None:
    _write_env_values(path, {key: value})


def _bounded_gain(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ValueError("output gain must be a number")
    if parsed < 0.0 or parsed > 2.0:
        raise ValueError("output gain must be between 0.0 and 2.0")
    return parsed


def _runtime_service_tiles(status: dict[str, Any], *, stale: bool = False) -> dict[str, dict[str, Any]]:
    """Translate the provider/MCP-neutral runtime contract into existing monitor tiles."""
    if not isinstance(status, dict) or not status:
        return {}

    engine = str(status.get("engine") or "unknown")
    provider = str(status.get("provider") or "").strip()
    model = str(status.get("model") or "").strip()
    voice = str(status.get("voice") or "").strip()
    ready = bool(status.get("ready")) and not stale
    identity = " / ".join(part for part in (provider, model, voice) if part)

    services: dict[str, dict[str, Any]] = {
        "Voice engine": {
            "status": "ready" if ready else ("offline" if stale else "starting"),
            "detail": f"{engine}{(' · ' + identity) if identity else ''}{' · stale status' if stale else ''}",
        }
    }

    for entry in status.get("mcp") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "MCP").strip() or "MCP"
        configured = str(entry.get("configured_transport") or "").strip()
        effective = str(entry.get("effective_transport") or "").strip()
        permission = str(entry.get("permission") or "").strip()
        healthy = entry.get("healthy")
        detail = str(entry.get("detail") or "").strip()
        transport = effective or configured or "unknown"
        if stale:
            state = "offline"
        elif healthy is True:
            state = "online"
        elif healthy is False:
            state = "offline"
        else:
            state = "unknown"
        parts = [f"transport={transport}"]
        if configured and configured != transport:
            parts.append(f"configured={configured}")
        if permission:
            parts.append(f"permission={permission}")
        if detail:
            parts.append(detail)
        if stale:
            parts.append("stale runtime status")
        services[f"MCP · {name}"] = {"status": state, "detail": " · ".join(parts)}
    return services


class WebMonitor(_BaseWebMonitor):
    """Single runtime-owned WebMonitor with common runtime controls."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._mcp_realtime_policy_save_handler: Callable[[str, dict[str, Any]], dict[str, Any]] = self._save_mcp_realtime_policy
        self._runtime_restart_handler: Callable[[], None] | None = None

    def set_mcp_realtime_policy_save_handler(self, handler: Callable[[str, dict[str, Any]], dict[str, Any]]) -> None:
        with self._lock:
            self._mcp_realtime_policy_save_handler = handler

    def set_runtime_restart_handler(self, handler: Callable[[], None] | None) -> None:
        with self._lock:
            self._runtime_restart_handler = handler

    def snapshot(self) -> dict[str, Any]:
        snapshot = super().snapshot()
        config = snapshot.get("config")
        if isinstance(config, dict):
            snapshot["config"] = _base.redact_mapping(config)
            snapshot["config_text"] = __import__("json").dumps(snapshot["config"], ensure_ascii=False, indent=2)
        payload = self._runtime_status()
        if not payload.get("available"):
            return snapshot
        runtime = payload.get("runtime") or {}
        stale = bool(payload.get("stale"))
        snapshot["runtime_status"] = runtime
        snapshot["runtime_status_stale"] = stale
        services = dict(snapshot.get("services") or {})
        services.update(_runtime_service_tiles(runtime, stale=stale))
        snapshot["services"] = services

        semantic_state = str(runtime.get("semantic_state") or "").strip().lower()
        if not stale and semantic_state == "starting":
            snapshot["environment_loading"] = {
                "active": True,
                "title": "Application de la configuration",
            }
        elif not stale and bool(runtime.get("ready")):
            snapshot["environment_loading"] = {"active": False, "title": ""}
        return snapshot

    def _save_mcp_realtime_policy(self, server_name: str, policy: dict[str, Any]) -> dict[str, Any]:
        safe_policy, refreshed_config = save_mcp_realtime_policy_from_snapshot(self.snapshot(), server_name, policy)
        self.update(mcp_config=refreshed_config)
        return {"ok": True, "server": server_name, "policy": safe_policy}

    def _runtime_status(self) -> dict[str, Any]:
        path = _runtime_status_file()
        if not path.is_file():
            return {"ok": False, "available": False, "status_file": str(path), "error": "runtime status is not available yet"}
        try:
            stat = path.stat()
            age_seconds = max(0.0, time.time() - stat.st_mtime)
            status = read_status_file(path)
        except Exception as error:
            return {"ok": False, "available": False, "status_file": str(path), "error": f"could not read runtime status: {error}"}
        stale = age_seconds > _runtime_status_stale_seconds()
        return {
            "ok": not stale,
            "available": True,
            "stale": stale,
            "age_seconds": age_seconds,
            "status_file": str(path),
            "runtime": status,
        }

    def _save_voice_engine(self, engine: str, *, realtime_model: str = "", realtime_voice: str = "") -> dict[str, Any]:
        snapshot = self.snapshot()
        config = snapshot.get("config") or {}
        env_values = dict(config.get("env") or {})
        connectivity = str(env_values.get("CONNECTIVITY_MODE") or "online").strip().lower()
        normalized = str(engine or "").strip().lower()
        allowed = VOICE_ENGINE_OFFLINE if connectivity == "offline" else VOICE_ENGINE_ONLINE
        if normalized not in allowed:
            expected = ", ".join(sorted(allowed))
            raise ValueError(f"voice_engine must be one of: {expected}")

        updates = {"VOICE_ENGINE": normalized}
        model = str(realtime_model or "").strip()
        voice = str(realtime_voice or "").strip()
        if normalized == "openai-realtime":
            model = model or str(env_values.get("OPENAI_REALTIME_MODEL") or DEFAULT_REALTIME_MODEL).strip()
            voice = voice or str(env_values.get("OPENAI_REALTIME_VOICE") or DEFAULT_REALTIME_VOICE).strip()
            if not model:
                raise ValueError("OPENAI_REALTIME_MODEL must not be empty")
            if not voice:
                raise ValueError("OPENAI_REALTIME_VOICE must not be empty")
            updates["OPENAI_REALTIME_MODEL"] = model
            updates["OPENAI_REALTIME_VOICE"] = voice

        env_file = _active_env_file_from_snapshot(snapshot)
        _write_env_values(env_file, updates)
        env_values.update(updates)
        self.update(env_values=env_values)
        return {
            "ok": True,
            "voice_engine": normalized,
            "realtime_model": model if normalized == "openai-realtime" else str(env_values.get("OPENAI_REALTIME_MODEL") or DEFAULT_REALTIME_MODEL).strip(),
            "realtime_voice": voice if normalized == "openai-realtime" else str(env_values.get("OPENAI_REALTIME_VOICE") or DEFAULT_REALTIME_VOICE).strip(),
            "connectivity_mode": connectivity,
            "restart_required": True,
            "message": "Voice engine settings saved. Restart LiveStageAssistant to apply them.",
        }

    def _save_voice_output_gains(self, cloud_gain: Any, local_gain: Any) -> dict[str, Any]:
        cloud = _bounded_gain(cloud_gain)
        local = _bounded_gain(local_gain)
        updates = {
            "CLOUD_TTS_OUTPUT_GAIN": f"{cloud:.2f}",
            "LOCAL_TTS_OUTPUT_GAIN": f"{local:.2f}",
        }
        written: list[str] = []
        for env_file in _profile_env_files():
            if not env_file.is_file():
                continue
            _write_env_values(env_file, updates)
            written.append(str(env_file))
        if not written:
            raise ValueError("no online/offline env profile exists for output-gain persistence")

        snapshot = self.snapshot()
        env_values = dict(((snapshot.get("config") or {}).get("env") or {}))
        env_values.update(updates)
        self.update(env_values=env_values)
        return {
            "ok": True,
            "cloud_gain": cloud,
            "local_gain": local,
            "profiles": written,
            "restart_required": True,
            "message": "Cloud and Local speech output gains saved. Restart LiveStageAssistant to apply them.",
        }

    def _request_runtime_restart(self) -> dict[str, Any]:
        with self._lock:
            handler = self._runtime_restart_handler
        if handler is None:
            raise RuntimeError("runtime restart is not available")
        self.set_environment_loading(True, "Application de la configuration")
        handler()
        return {"ok": True, "restart_requested": True, "message": "Runtime engine reload requested."}

    def start(self, host: str = "127.0.0.1", port: int = 8765) -> tuple[str, int]:
        monitor = self
        with _START_PATCH_LOCK:
            original_http_server = _base.ThreadingHTTPServer

            def server_factory(server_address, handler_class):
                class RealtimePolicyHandler(handler_class):
                    def do_GET(self) -> None:
                        parsed = _base.urlparse(self.path)
                        if parsed.path != "/api/runtime-status":
                            super().do_GET()
                            return
                        if self._auth_required(parsed.path):
                            self._send_auth_required()
                            return
                        self._send_json(monitor._runtime_status())

                    def do_POST(self) -> None:
                        parsed = _base.urlparse(self.path)
                        if parsed.path not in {"/api/mcp-realtime-policy", "/api/voice-engine", "/api/voice-output-gains", "/api/runtime-restart"}:
                            super().do_POST()
                            return
                        if self._auth_required(parsed.path):
                            self._send_auth_required()
                            return
                        if parsed.path == "/api/voice-engine":
                            self._handle_voice_engine_save()
                            return
                        if parsed.path == "/api/voice-output-gains":
                            self._handle_voice_output_gains_save()
                            return
                        if parsed.path == "/api/runtime-restart":
                            self._handle_runtime_restart()
                            return
                        self._handle_mcp_realtime_policy_save()

                    def _handle_voice_engine_save(self) -> None:
                        payload = self._read_json_body(max_bytes=16 * 1024)
                        if payload is None:
                            return
                        try:
                            result = monitor._save_voice_engine(
                                str(payload.get("voice_engine") or ""),
                                realtime_model=str(payload.get("realtime_model") or ""),
                                realtime_voice=str(payload.get("realtime_voice") or ""),
                            )
                        except ValueError as error:
                            self._send_json_error(400, {"ok": False, "error": {"message": str(error)}})
                            return
                        except Exception as error:  # pragma: no cover
                            self._send_json_error(500, {"ok": False, "error": {"message": f"Could not save voice engine: {error}"}})
                            return
                        self._send_json(result)

                    def _handle_voice_output_gains_save(self) -> None:
                        payload = self._read_json_body(max_bytes=16 * 1024)
                        if payload is None:
                            return
                        try:
                            result = monitor._save_voice_output_gains(payload.get("cloud_gain"), payload.get("local_gain"))
                        except ValueError as error:
                            self._send_json_error(400, {"ok": False, "error": {"message": str(error)}})
                            return
                        except Exception as error:  # pragma: no cover
                            self._send_json_error(500, {"ok": False, "error": {"message": f"Could not save voice output gains: {error}"}})
                            return
                        self._send_json(result)

                    def _handle_runtime_restart(self) -> None:
                        payload = self._read_json_body(max_bytes=4 * 1024)
                        if payload is None:
                            return
                        try:
                            result = monitor._request_runtime_restart()
                        except Exception as error:
                            monitor.set_environment_loading(False)
                            self._send_json_error(503, {"ok": False, "error": {"message": str(error)}})
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
