"""Runtime-owned WebMonitor wrapper for common RV2D/RV8 controls."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values
import tempfile
import threading
from typing import Any, Callable

try:
    from . import web_monitor_base as _base
    from .mcp_realtime_web_endpoint import (
        delete_mcp_server_from_snapshot,
        mcp_registry_from_snapshot,
        save_mcp_realtime_policy_from_snapshot,
        save_mcp_server_from_snapshot,
        test_mcp_server_from_snapshot,
    )
    from .runtime_status import read_status_file
    from .realtime.browser_auth import create_openai_browser_client_secret
    from .session_context import DEFAULT_CONTEXT_DIR, SessionContextStore
except ImportError:  # pragma: no cover - direct script fallback
    import web_monitor_base as _base
    from mcp_realtime_web_endpoint import delete_mcp_server_from_snapshot, mcp_registry_from_snapshot, save_mcp_realtime_policy_from_snapshot, save_mcp_server_from_snapshot, test_mcp_server_from_snapshot
    from runtime_status import read_status_file
    from realtime.browser_auth import create_openai_browser_client_secret
    from session_context import DEFAULT_CONTEXT_DIR, SessionContextStore

for _name in dir(_base):
    if not _name.startswith("_") and _name != "WebMonitor":
        globals()[_name] = getattr(_base, _name)

_BaseWebMonitor = _base.WebMonitor
_START_PATCH_LOCK = threading.Lock()
DEFAULT_RUNTIME_STATUS_FILE = "/tmp/livestageassistant-runtime-status.json"
REALTIME_CHAT_BOOTSTRAP = """<script id=\"lsa-realtime-chat-bootstrap\">
(() => {
  const configured = String(window.LSA_BASE_PATH || "").replace(/\/+$/, "");
  let base = configured;
  if (!base) {
    let path = window.location.pathname || "/";
    path = path.replace(/\/index\.html$/, "");
    if (path !== "/" && path.endsWith("/")) path = path.slice(0, -1);
    base = path === "/" ? "" : path;
  }
  const script = document.createElement("script");
  script.src = `${base}/assets/web/realtime-chat.js`;
  script.defer = true;
  document.currentScript.after(script);
})();
</script>"""


def _runtime_status_file() -> Path:
    return Path(os.getenv("LSA_RUNTIME_STATUS_FILE", DEFAULT_RUNTIME_STATUS_FILE)).expanduser()


def _write_env_values(path: Path, values: dict[str, str]) -> None:
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
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write("\n".join(output) + "\n")
        tmp_path = Path(handle.name)
    os.chmod(tmp_path, original_mode)
    tmp_path.replace(path)


def _display_transport(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return "https" if normalized == "native" else normalized


def _runtime_service_tiles(status: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(status, dict) or not status:
        return {}
    engine = str(status.get("engine") or "unknown")
    provider = str(status.get("provider") or "").strip()
    model = str(status.get("model") or "").strip()
    voice = str(status.get("voice") or "").strip()
    ready = bool(status.get("ready"))
    identity = " / ".join(part for part in (provider, model, voice) if part)
    services: dict[str, dict[str, Any]] = {"Voice engine": {"status": "ready" if ready else "starting", "detail": f"{engine}{(' · ' + identity) if identity else ''}"}}
    for entry in status.get("mcp") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "MCP").strip() or "MCP"
        configured = str(entry.get("configured_transport") or "").strip()
        enabled = entry.get("enabled", True) is not False
        effective = str(entry.get("effective_transport") or "").strip()
        permission = str(entry.get("permission") or "").strip()
        healthy = entry.get("healthy")
        detail = str(entry.get("detail") or "").strip()
        transport = effective or configured or "unknown"
        state = "disabled" if not enabled else "online" if healthy is True else "offline" if healthy is False else "unknown"
        parts = ["disabled"] if not enabled else [f"transport={_display_transport(transport)}"]
        if configured and configured != transport:
            parts.append(f"configured={_display_transport(configured)}")
        if permission:
            parts.append(f"permission={permission}")
        if detail:
            parts.append(detail)
        services[f"MCP · {name}"] = {"status": state, "detail": " · ".join(parts)}
    return services


class WebMonitor(_BaseWebMonitor):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._mcp_realtime_policy_save_handler: Callable[[str, dict[str, Any]], dict[str, Any]] = self._save_mcp_realtime_policy
        self._runtime_restart_handler: Callable[[], None] | None = None
        self._runtime_reload_state_provider: Callable[[], bool] = lambda: False

    def render_index_html(self) -> str:
        html = super().render_index_html()
        if "lsa-realtime-chat-bootstrap" in html:
            return html
        if "</body>" in html:
            return html.replace("</body>", f"  {REALTIME_CHAT_BOOTSTRAP}\n</body>")
        return html + REALTIME_CHAT_BOOTSTRAP

    def set_mcp_realtime_policy_save_handler(self, handler: Callable[[str, dict[str, Any]], dict[str, Any]]) -> None:
        with self._lock:
            self._mcp_realtime_policy_save_handler = handler

    def set_runtime_restart_handler(self, handler: Callable[[], None] | None) -> None:
        with self._lock:
            self._runtime_restart_handler = handler

    def set_runtime_reload_state_provider(self, provider: Callable[[], bool] | None) -> None:
        with self._lock:
            self._runtime_reload_state_provider = provider or (lambda: False)

    def _hydrate_session_context_if_needed(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        context = snapshot.get("session_context") if isinstance(snapshot, dict) else None
        sessions = context.get("sessions") if isinstance(context, dict) else None
        active_id = str(context.get("active_id") or "") if isinstance(context, dict) else ""
        if active_id and isinstance(sessions, list) and sessions:
            return snapshot
        try:
            store = self._session_store_for_realtime_chat()
            context_snapshot = store.snapshot()
            self.replace_dialogue(context_snapshot.get("messages") or [])
            try:
                size = int(str(self._active_env_values().get("SESSION_CONTEXT_SIZE") or "6000"))
            except (TypeError, ValueError):
                size = 6000
            self.set_context_state(context_snapshot, session_context_size=size)
            return super().snapshot()
        except Exception as error:
            self.append_log(f"Initial session snapshot skipped: {error}\n", source="web")
            return snapshot

    def snapshot(self) -> dict[str, Any]:
        snapshot = self._hydrate_session_context_if_needed(super().snapshot())
        config = snapshot.get("config")
        if isinstance(config, dict):
            snapshot["config"] = _base.redact_mapping(config)
            snapshot["config_text"] = __import__("json").dumps(snapshot["config"], ensure_ascii=False, indent=2)
        with self._lock:
            reload_state_provider = self._runtime_reload_state_provider
        try:
            loading = bool(reload_state_provider())
        except Exception:
            loading = False
        snapshot["environment_loading"] = {"active": loading, "title": "Application de la configuration" if loading else ""}
        if not loading:
            self.set_environment_loading(False)
        try:
            snapshot["mcp_registry"] = mcp_registry_from_snapshot(snapshot)
        except Exception:
            snapshot["mcp_registry"] = []
        payload = self._runtime_status()
        if not payload.get("available"):
            return snapshot
        runtime = payload.get("runtime") or {}
        snapshot["runtime_status"] = runtime
        services = dict(snapshot.get("services") or {})
        services.update(_runtime_service_tiles(runtime))
        snapshot["services"] = services
        return snapshot

    def _refresh_mcp_snapshot(self, config: dict[str, Any]) -> None:
        self.update(mcp_config=config)

    def _save_mcp_realtime_policy(self, server_name: str, policy: dict[str, Any]) -> dict[str, Any]:
        safe_policy, refreshed_config = save_mcp_realtime_policy_from_snapshot(self.snapshot(), server_name, policy)
        self._refresh_mcp_snapshot(refreshed_config)
        return {"ok": True, "server": server_name, "policy": safe_policy, "restart_required": True}

    def _save_mcp_server(self, payload: dict[str, Any], *, existing_name: str = "") -> dict[str, Any]:
        safe_server, refreshed_config = save_mcp_server_from_snapshot(self.snapshot(), payload, existing_name=existing_name)
        self._refresh_mcp_snapshot(refreshed_config)
        return {"ok": True, "server": safe_server, "restart_required": True}

    def _delete_mcp_server(self, server_name: str) -> dict[str, Any]:
        deleted, refreshed_config = delete_mcp_server_from_snapshot(self.snapshot(), server_name)
        self._refresh_mcp_snapshot(refreshed_config)
        return {"ok": True, "deleted": deleted, "restart_required": True}

    def _test_mcp_server(self, server_name: str) -> dict[str, Any]:
        return test_mcp_server_from_snapshot(self.snapshot(), server_name)

    def _read_api_key_file(self, secret_path: str, env_file: Path) -> str:
        raw_path = Path(secret_path).expanduser()
        candidates = [raw_path] if raw_path.is_absolute() else [env_file.parent / raw_path, Path.cwd() / raw_path]
        tried: list[str] = []
        for candidate in candidates:
            tried.append(str(candidate))
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8").strip()
        raise RuntimeError(f"could not read OPENAI_API_KEY_FILE; tried: {', '.join(tried)}")

    def _active_env_values(self) -> dict[str, Any]:
        snapshot = super().snapshot()
        env_file = Path(str(snapshot.get("env_file") or "")).expanduser()
        if env_file.is_file():
            return dict(dotenv_values(env_file))
        env = (snapshot.get("config") or {}).get("env") if isinstance(snapshot.get("config"), dict) else {}
        return dict(env or {}) if isinstance(env, dict) else {}

    def _browser_realtime_secret(self) -> dict[str, Any]:
        snapshot = super().snapshot()
        env_file = Path(str(snapshot.get("env_file") or "")).expanduser()
        if not env_file.is_file():
            raise RuntimeError("active runtime profile is unavailable")
        values = dict(dotenv_values(env_file))
        if str(values.get("VOICE_ENGINE") or "").strip().lower() != "openai-realtime":
            raise ValueError("browser WebRTC is available only with OpenAI Realtime selected")
        secret_path = str(values.get("OPENAI_API_KEY_FILE") or "").strip()
        api_key = str(values.get("OPENAI_API_KEY") or "").strip()
        if not api_key and secret_path:
            try:
                api_key = self._read_api_key_file(secret_path, env_file)
            except OSError as exc:
                raise RuntimeError(f"could not read OPENAI_API_KEY_FILE: {exc}") from exc
        return create_openai_browser_client_secret(
            api_key,
            model=str(values.get("OPENAI_REALTIME_MODEL") or "gpt-realtime-2.1").strip(),
            voice=str(values.get("OPENAI_REALTIME_VOICE") or "marin").strip(),
            instructions=str(values.get("ASSISTANT_SYSTEM_PROMPT") or "").strip(),
        )

    def _runtime_status(self) -> dict[str, Any]:
        path = _runtime_status_file()
        if not path.is_file():
            return {"ok": False, "available": False, "status_file": str(path), "error": "runtime status is not available yet"}
        try:
            status = read_status_file(path)
        except Exception as error:
            return {"ok": False, "available": False, "status_file": str(path), "error": f"could not read runtime status: {error}"}
        return {"ok": True, "available": True, "status_file": str(path), "runtime": status}

    def _request_runtime_restart(self) -> dict[str, Any]:
        with self._lock:
            handler = self._runtime_restart_handler
        if handler is None:
            raise RuntimeError("runtime restart is not available")
        handler()
        return {"ok": True, "restart_requested": True, "message": "Runtime engine reload requested."}

    def _session_store_for_realtime_chat(self) -> SessionContextStore:
        values = self._active_env_values()
        context_dir = str(values.get("SESSION_CONTEXT_DIR") or os.getenv("SESSION_CONTEXT_DIR") or DEFAULT_CONTEXT_DIR).strip()
        context_path = Path(context_dir).expanduser()
        if not context_path.is_absolute():
            context_path = Path.cwd() / context_path
        try:
            summary_max_chars = int(values.get("SESSION_CONTEXT_SUMMARY_MAX_CHARS") or os.getenv("SESSION_CONTEXT_SUMMARY_MAX_CHARS") or 12000)
        except (TypeError, ValueError):
            summary_max_chars = 12000
        return SessionContextStore(context_path, summary_max_chars=summary_max_chars)

    def _append_realtime_chat_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        role = str(payload.get("role") or "").strip().lower()
        text = str(payload.get("text") or "").strip()
        if role not in {"user", "assistant"}:
            raise ValueError("role must be user or assistant")
        if not text:
            raise ValueError("text is required")
        speak = bool(payload.get("speak"))
        self.append_dialogue(role, text, speak=speak)
        persisted = False
        try:
            store = self._session_store_for_realtime_chat()
            store.append_message(role, text)
            self.set_context_state(store.snapshot())
            persisted = True
        except Exception as error:
            self.append_log(f"Realtime chat session persistence skipped: {error}\n", source="web")
        if role == "assistant":
            self.set_assistant_busy(False)
        return {"ok": True, "role": role, "persisted": persisted}

    def _set_realtime_chat_state(self, payload: dict[str, Any]) -> dict[str, Any]:
        busy = bool(payload.get("assistant_busy"))
        self.set_assistant_busy(busy)
        return {"ok": True, "assistant_busy": busy}

    def start(self, host: str = "127.0.0.1", port: int = 8765) -> tuple[str, int]:
        monitor = self
        with _START_PATCH_LOCK:
            original_http_server = _base.ThreadingHTTPServer

            def server_factory(server_address, handler_class):
                class RealtimePolicyHandler(handler_class):
                    def _send_isolation_headers(self) -> None:
                        return

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
                        routes = {
                            "/api/mcp-realtime-policy",
                            "/api/mcp-server",
                            "/api/mcp-test",
                            "/api/runtime-restart",
                            "/api/realtime-browser-secret",
                            "/api/realtime-chat-message",
                            "/api/realtime-chat-state",
                        }
                        if parsed.path not in routes:
                            super().do_POST()
                            return
                        if self._auth_required(parsed.path):
                            self._send_auth_required()
                            return
                        if parsed.path == "/api/runtime-restart":
                            self._handle_runtime_restart(); return
                        if parsed.path == "/api/realtime-browser-secret":
                            self._handle_realtime_browser_secret(); return
                        if parsed.path == "/api/realtime-chat-message":
                            self._handle_realtime_chat_message(); return
                        if parsed.path == "/api/realtime-chat-state":
                            self._handle_realtime_chat_state(); return
                        if parsed.path == "/api/mcp-server":
                            self._handle_mcp_server(); return
                        if parsed.path == "/api/mcp-test":
                            self._handle_mcp_test(); return
                        self._handle_mcp_realtime_policy_save()

                    def _handle_realtime_browser_secret(self) -> None:
                        payload = self._read_json_body(max_bytes=4 * 1024)
                        if payload is None: return
                        try:
                            result = monitor._browser_realtime_secret()
                        except ValueError as error:
                            self._send_json_error(400, {"ok": False, "error": {"message": str(error)}}); return
                        except Exception as error:
                            self._send_json_error(503, {"ok": False, "error": {"message": str(error)}}); return
                        self._send_json({"ok": True, "client_secret": result})

                    def _handle_realtime_chat_message(self) -> None:
                        payload = self._read_json_body(max_bytes=64 * 1024)
                        if payload is None: return
                        try:
                            result = monitor._append_realtime_chat_message(payload)
                        except ValueError as error:
                            self._send_json_error(400, {"ok": False, "error": {"message": str(error)}}); return
                        except Exception as error:
                            self._send_json_error(500, {"ok": False, "error": {"message": f"Could not append realtime chat message: {error}"}}); return
                        self._send_json(result)

                    def _handle_realtime_chat_state(self) -> None:
                        payload = self._read_json_body(max_bytes=4 * 1024)
                        if payload is None: return
                        try:
                            result = monitor._set_realtime_chat_state(payload)
                        except Exception as error:
                            self._send_json_error(500, {"ok": False, "error": {"message": f"Could not update realtime chat state: {error}"}}); return
                        self._send_json(result)

                    def _handle_runtime_restart(self) -> None:
                        payload = self._read_json_body(max_bytes=4 * 1024)
                        if payload is None: return
                        try:
                            result = monitor._request_runtime_restart()
                        except Exception as error:
                            self._send_json_error(503, {"ok": False, "error": {"message": str(error)}}); return
                        self._send_json(result)

                    def _handle_mcp_test(self) -> None:
                        payload = self._read_json_body(max_bytes=8 * 1024)
                        if payload is None: return
                        name = str(payload.get("server") or "").strip()
                        if not name:
                            self._send_json_error(400, {"ok": False, "error": {"message": "server is required"}}); return
                        try:
                            result = monitor._test_mcp_server(name)
                        except ValueError as error:
                            self._send_json_error(400, {"ok": False, "error": {"message": str(error)}}); return
                        except Exception as error:
                            self._send_json_error(500, {"ok": False, "error": {"message": f"MCP test failed: {error}"}}); return
                        self._send_json(result)

                    def _handle_mcp_server(self) -> None:
                        payload = self._read_json_body(max_bytes=64 * 1024)
                        if payload is None: return
                        action = str(payload.get("action") or "save").strip().lower()
                        try:
                            if action == "delete":
                                result = monitor._delete_mcp_server(str(payload.get("server") or ""))
                            elif action in {"create", "save", "update"}:
                                server_payload = payload.get("server")
                                if not isinstance(server_payload, dict):
                                    raise ValueError("server must be an object")
                                result = monitor._save_mcp_server(server_payload, existing_name=str(payload.get("existing_name") or ""))
                            else:
                                raise ValueError("action must be create, update, or delete")
                        except ValueError as error:
                            self._send_json_error(400, {"ok": False, "error": {"message": str(error)}}); return
                        except Exception as error:
                            self._send_json_error(500, {"ok": False, "error": {"message": f"Could not update MCP server: {error}"}}); return
                        self._send_json(result)

                    def _handle_mcp_realtime_policy_save(self) -> None:
                        payload = self._read_json_body(max_bytes=64 * 1024)
                        if payload is None: return
                        server_name = str(payload.get("server") or "").strip()
                        policy = payload.get("policy")
                        if not server_name:
                            self._send_json_error(400, {"ok": False, "error": {"message": "server is required"}}); return
                        if not isinstance(policy, dict):
                            self._send_json_error(400, {"ok": False, "error": {"message": "policy must be an object"}}); return
                        try:
                            result = monitor._mcp_realtime_policy_save_handler(server_name, policy)
                        except ValueError as error:
                            self._send_json_error(400, {"ok": False, "error": {"message": str(error)}}); return
                        except Exception as error:
                            self._send_json_error(500, {"ok": False, "error": {"message": f"Could not save MCP realtime policy: {error}"}}); return
                        self._send_json(result)

                return original_http_server(server_address, RealtimePolicyHandler)

            _base.ThreadingHTTPServer = server_factory
            try:
                return super().start(host, port)
            finally:
                _base.ThreadingHTTPServer = original_http_server
