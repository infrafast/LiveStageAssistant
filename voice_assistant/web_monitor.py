"""Small read-only web monitor for the voice assistant runtime."""

from __future__ import annotations

import base64
import binascii
from collections import deque
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import mimetypes
from pathlib import Path
import re
import select
import socket
import struct
import sys
import threading
import time
from typing import Any, Callable, TextIO
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import parse_qs, quote, unquote, urlparse


SECRET_KEY_MARKERS = (
    "API_KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASS",
    "CONNECTION_STRING",
)


def redact_config_value(key: str, value: Any) -> Any:
    """Mask sensitive values while keeping non-secret runtime configuration visible."""
    upper_key = key.upper()
    if any(marker in upper_key for marker in SECRET_KEY_MARKERS):
        if value in (None, ""):
            return value
        return "***redacted***"
    if isinstance(value, dict):
        return redact_mapping(value)
    if isinstance(value, list):
        return [redact_config_value(key, item) for item in value]
    return value


def concise_web_tts_error(error: Exception | str) -> dict[str, str]:
    """Return a short user-facing message for browser TTS failures."""
    raw = str(error or "").strip()
    lowered = raw.lower()
    detail_match = re.search(r"['\"]message['\"]\s*:\s*['\"]([^'\"]+)['\"]", raw)
    detail = detail_match.group(1).strip() if detail_match else ""

    if any(marker in lowered for marker in ("quota_exceeded", "insufficient_quota", "exceeds your quota")):
        return {
            "kind": "quota",
            "message": "Plus de crédit TTS disponible.",
            "detail": detail or "Le fournisseur TTS indique que le quota ou les crédits sont épuisés.",
        }
    if any(
        marker in lowered
        for marker in ("invalid_api_key", "invalid api key", "unauthorized", "status_code: 401", "status code: 401")
    ):
        return {
            "kind": "auth",
            "message": "Clé API TTS invalide ou refusée.",
            "detail": detail or "Le fournisseur TTS a refusé l'authentification.",
        }
    if any(
        marker in lowered
        for marker in ("rate_limit", "rate limit", "too many requests", "status_code: 429", "status code: 429")
    ):
        return {
            "kind": "rate_limit",
            "message": "Limite TTS atteinte, réessaie dans un moment.",
            "detail": detail or "Le fournisseur TTS limite temporairement les requêtes.",
        }
    if any(marker in lowered for marker in ("billing", "payment", "credit", "credits remaining")):
        return {
            "kind": "billing",
            "message": "Problème de crédit ou facturation TTS.",
            "detail": detail or "Le fournisseur TTS signale un problème de crédit ou facturation.",
        }

    text = re.sub(r"<[^>]+>", " ", raw)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 180:
        text = text[:177].rstrip() + "..."
    return {
        "kind": "error",
        "message": "Erreur TTS web.",
        "detail": text or "Le TTS web n'a pas pu générer l'audio.",
    }


def redact_mapping(values: dict[str, Any]) -> dict[str, Any]:
    return {key: redact_config_value(key, value) for key, value in values.items()}


def build_mcp_server_admin_frames(mcp_config: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return browser-embeddable MCP admin page targets from the active config."""
    servers = (mcp_config or {}).get("mcpServers")
    if not isinstance(servers, dict):
        return []

    frames: list[dict[str, Any]] = []
    for name, server_config in sorted(servers.items()):
        entry: dict[str, Any] = {
            "name": str(name),
            "type": "unknown",
            "url": "",
            "admin_url": "",
            "embeddable": False,
            "auth_required": False,
            "detail": "",
        }
        if not isinstance(server_config, dict):
            entry["detail"] = "invalid MCP server config"
            frames.append(entry)
            continue

        server_type = str(server_config.get("type") or "stdio")
        entry["type"] = server_type
        url = str(server_config.get("url") or "").strip()
        if not url:
            entry["detail"] = "stdio/local MCP server; no browser admin URL"
            frames.append(entry)
            continue

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            entry["url"] = url
            entry["detail"] = "unsupported MCP URL"
            frames.append(entry)
            continue

        admin_url = parsed._replace(path="/mcp", query="", fragment="").geturl()
        proxy_admin_url = f"/api/mcp-admin/{quote(str(name), safe='')}/mcp"
        headers = server_config.get("headers")
        auth_required = False
        if isinstance(headers, dict):
            auth_required = any(str(key).lower() == "authorization" and value for key, value in headers.items())

        entry.update(
            {
                "url": url,
                "admin_url": admin_url,
                "proxy_admin_url": proxy_admin_url,
                "embeddable": True,
                "auth_required": auth_required,
                "detail": "proxied through LiveStageAssistant backend",
            }
        )
        frames.append(entry)

    return frames


def build_mcp_admin_proxy_targets(mcp_config: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Build backend-only proxy targets for configured HTTP MCP servers."""
    servers = (mcp_config or {}).get("mcpServers")
    if not isinstance(servers, dict):
        return {}

    targets: dict[str, dict[str, Any]] = {}
    for name, server_config in servers.items():
        if not isinstance(server_config, dict):
            continue
        url = str(server_config.get("url") or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        headers: dict[str, str] = {}
        raw_headers = server_config.get("headers")
        if isinstance(raw_headers, dict):
            for key, value in raw_headers.items():
                if value is not None:
                    headers[str(key)] = str(value)
        targets[str(name)] = {
            "scheme": parsed.scheme,
            "netloc": parsed.netloc,
            "headers": headers,
        }
    return targets


class TeeStream:
    """Mirror writes to the original stream and to the monitor log buffer."""

    def __init__(self, original: TextIO, monitor: "WebMonitor", stream_name: str):
        self.original = original
        self.monitor = monitor
        self.stream_name = stream_name

    def write(self, value: str) -> int:
        self.monitor.write_console(value, self.original, source=self.stream_name)
        return len(value)

    def flush(self) -> None:
        self.original.flush()

    def isatty(self) -> bool:
        return self.original.isatty()

    def fileno(self) -> int:
        return self.original.fileno()

    @property
    def encoding(self) -> str | None:
        return self.original.encoding

    def __getattr__(self, name: str) -> Any:
        return getattr(self.original, name)


class WebMonitor:
    """Thread-safe runtime state served over a tiny local HTTP server."""

    def __init__(self, max_log_chars: int = 200_000, max_messages: int = 80):
        self.max_log_chars = max_log_chars
        self.max_messages = max_messages
        self._lock = threading.RLock()
        self._log_chunks: deque[str] = deque()
        self._log_chars = 0
        self._messages: deque[dict[str, Any]] = deque()
        self._next_message_id = 1
        self._injected_commands: deque[str] = deque()
        self._cancel_requested = False
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._stdout_original: TextIO | None = None
        self._stderr_original: TextIO | None = None
        self._logging_handler_streams: list[tuple[logging.StreamHandler, TextIO]] = []
        self._llm_options_handler: Callable[[str | None], dict[str, Any]] | None = None
        self._llm_config_save_handler: Callable[
            [str, str, str, str, str, str, str, str, int, bool, bool, str, str, str, str, str, float],
            dict[str, Any],
        ] | None = None
        self._cloud_api_status_handler: Callable[[], dict[str, Any]] | None = None
        self._env_profile_handler: Callable[[], dict[str, Any]] | None = None
        self._env_profile_switch_handler: Callable[[str], dict[str, Any]] | None = None
        self._remote_screen_save_handler: Callable[[str], dict[str, Any]] | None = None
        self._session_context_list_handler: Callable[[], dict[str, Any]] | None = None
        self._session_context_new_handler: Callable[[str | None], dict[str, Any]] | None = None
        self._session_context_select_handler: Callable[[str], dict[str, Any]] | None = None
        self._session_context_rename_handler: Callable[[str, str], dict[str, Any]] | None = None
        self._session_context_clear_handler: Callable[[str], dict[str, Any]] | None = None
        self._session_context_save_handler: Callable[[str], dict[str, Any]] | None = None
        self._session_context_delete_handler: Callable[[str], dict[str, Any]] | None = None
        self._cancel_handler: Callable[[], None] | None = None
        self._web_audio_transcribe_handler: Callable[[bytes, str, bool], dict[str, Any]] | None = None
        self._web_audio_tts_handler: Callable[[str], dict[str, Any]] | None = None
        self._mcp_admin_proxy_targets: dict[str, dict[str, Any]] = {}
        self._started_at = time.time()
        self._snapshot: dict[str, Any] = {
            "mode": "unknown",
            "env_file": None,
            "internet": "unknown",
            "services": {},
            "config": {},
            "config_text": "{}",
            "mcp_servers": [],
            "prompt": "",
            "session_context": {"active_id": "", "sessions": [], "current": {}, "messages": []},
            "session_context_size": 6000,
            "assistant_busy": False,
            "environment_loading": {"active": False, "title": ""},
            "remote_screen": {"vnc_url": "vnc://192.168.0.160:5900?password=ronron"},
            "web_audio": {"enabled": False, "stt_enabled": False, "tts_enabled": False},
            "updated_at": time.time(),
        }

    def set_llm_config_handlers(
        self,
        *,
        options_handler: Callable[[str | None], dict[str, Any]],
        save_handler: Callable[[str, str, str, str, str, str, str, str, int, bool, bool, str, str, str, str, str, float], dict[str, Any]],
    ) -> None:
        """Register callbacks used by the web UI to list and save LLM settings."""
        with self._lock:
            self._llm_options_handler = options_handler
            self._llm_config_save_handler = save_handler

    def set_cloud_api_status_handler(self, handler: Callable[[], dict[str, Any]]) -> None:
        """Register callback used by the web UI to inspect cloud API quota/status."""
        with self._lock:
            self._cloud_api_status_handler = handler

    def set_env_profile_handlers(
        self,
        *,
        list_handler: Callable[[], dict[str, Any]],
        switch_handler: Callable[[str], dict[str, Any]],
    ) -> None:
        """Register callbacks used by the web UI to list and switch env profiles."""
        with self._lock:
            self._env_profile_handler = list_handler
            self._env_profile_switch_handler = switch_handler

    def set_remote_screen_handler(self, save_handler: Callable[[str], dict[str, Any]]) -> None:
        """Register callback used by the web UI to save remote-screen settings."""
        with self._lock:
            self._remote_screen_save_handler = save_handler

    def set_session_context_handlers(
        self,
        *,
        list_handler: Callable[[], dict[str, Any]],
        new_handler: Callable[[str | None], dict[str, Any]],
        select_handler: Callable[[str], dict[str, Any]],
        rename_handler: Callable[[str, str], dict[str, Any]],
        clear_handler: Callable[[str], dict[str, Any]],
        save_handler: Callable[[str], dict[str, Any]],
        delete_handler: Callable[[str], dict[str, Any]],
    ) -> None:
        """Register callbacks used by the web UI to list/create/select chat sessions."""
        with self._lock:
            self._session_context_list_handler = list_handler
            self._session_context_new_handler = new_handler
            self._session_context_select_handler = select_handler
            self._session_context_rename_handler = rename_handler
            self._session_context_clear_handler = clear_handler
            self._session_context_save_handler = save_handler
            self._session_context_delete_handler = delete_handler

    def set_web_audio_handlers(
        self,
        *,
        transcribe_handler: Callable[[bytes, str, bool], dict[str, Any]] | None = None,
        tts_handler: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        """Register callbacks used by the web UI for optional browser audio."""
        with self._lock:
            self._web_audio_transcribe_handler = transcribe_handler
            self._web_audio_tts_handler = tts_handler

    def set_cancel_handler(self, handler: Callable[[], None]) -> None:
        """Register a callback invoked when the web UI requests cancellation."""
        with self._lock:
            self._cancel_handler = handler

    def install_console_capture(self) -> None:
        with self._lock:
            if self._stdout_original is not None:
                return
            self._stdout_original = sys.stdout
            self._stderr_original = sys.stderr
            sys.stdout = TeeStream(sys.stdout, self, "stdout")
            sys.stderr = TeeStream(sys.stderr, self, "stderr")
            self._capture_existing_logging_handlers()

    def restore_console_capture(self) -> None:
        with self._lock:
            if self._stdout_original is not None:
                sys.stdout = self._stdout_original
                self._stdout_original = None
            if self._stderr_original is not None:
                sys.stderr = self._stderr_original
                self._stderr_original = None
            for handler, stream in self._logging_handler_streams:
                try:
                    handler.setStream(stream)
                except ValueError:
                    handler.stream = stream
            self._logging_handler_streams.clear()

    def _capture_existing_logging_handlers(self) -> None:
        """Route already-created logging handlers through the monitor tee streams."""
        original_streams = {
            self._stdout_original: sys.stdout,
            self._stderr_original: sys.stderr,
        }
        for logger in [logging.getLogger(), *logging.Logger.manager.loggerDict.values()]:
            if not isinstance(logger, logging.Logger):
                continue
            for handler in logger.handlers:
                if not isinstance(handler, logging.StreamHandler):
                    continue
                stream = getattr(handler, "stream", None)
                replacement = original_streams.get(stream)
                if replacement is None:
                    continue
                self._logging_handler_streams.append((handler, stream))
                try:
                    handler.setStream(replacement)
                except ValueError:
                    handler.stream = replacement

    def start(self, host: str = "127.0.0.1", port: int = 8765) -> tuple[str, int]:
        with self._lock:
            if self._server:
                return self._server.server_address

            monitor = self

            class MonitorHandler(BaseHTTPRequestHandler):
                def do_GET(self) -> None:
                    parsed = urlparse(self.path)
                    if parsed.path in {"/", "/index.html"}:
                        self._send_text(INDEX_HTML, "text/html; charset=utf-8")
                        return
                    if parsed.path == "/vnc.html":
                        self._send_text(VNC_HTML, "text/html; charset=utf-8")
                        return
                    if parsed.path == "/api/vnc-proxy":
                        self._handle_vnc_proxy(parsed.query)
                        return
                    if parsed.path == "/api/vnc-check":
                        self._handle_vnc_check(parsed.query)
                        return
                    if parsed.path.startswith("/api/mcp-admin/"):
                        self._handle_mcp_admin_proxy("GET", parsed.path, parsed.query)
                        return
                    if parsed.path == "/api/snapshot":
                        self._send_json(monitor.snapshot())
                        return
                    if parsed.path == "/api/llm-options":
                        self._handle_llm_options(parsed.query)
                        return
                    if parsed.path == "/api/cloud-api-status":
                        self._handle_cloud_api_status()
                        return
                    if parsed.path == "/api/env-profiles":
                        self._handle_env_profiles()
                        return
                    if parsed.path == "/api/session-context":
                        self._handle_session_context_list()
                        return
                    if parsed.path.startswith("/static/"):
                        self._handle_static(parsed.path)
                        return
                    if parsed.path.startswith("/assets/"):
                        self._handle_asset(parsed.path)
                        return
                    self.send_error(404)

                def do_HEAD(self) -> None:
                    parsed = urlparse(self.path)
                    if parsed.path in {"/", "/index.html"}:
                        self._send_text(INDEX_HTML, "text/html; charset=utf-8", send_body=False)
                        return
                    if parsed.path == "/vnc.html":
                        self._send_text(VNC_HTML, "text/html; charset=utf-8", send_body=False)
                        return
                    if parsed.path.startswith("/api/mcp-admin/"):
                        self._handle_mcp_admin_proxy("HEAD", parsed.path, parsed.query, send_body=False)
                        return
                    if parsed.path.startswith("/static/"):
                        self._handle_static(parsed.path, send_body=False)
                        return
                    if parsed.path.startswith("/assets/"):
                        self._handle_asset(parsed.path, send_body=False)
                        return
                    self.send_error(404)

                def do_POST(self) -> None:
                    parsed = urlparse(self.path)
                    if parsed.path.startswith("/api/mcp-admin/"):
                        self._handle_mcp_admin_proxy("POST", parsed.path, parsed.query)
                        return
                    if self.path == "/api/inject-command":
                        self._handle_inject_command()
                        return
                    if self.path == "/api/cancel-command":
                        self._handle_cancel_command()
                        return
                    if self.path == "/api/web-transcribe":
                        self._handle_web_transcribe()
                        return
                    if self.path == "/api/web-tts":
                        self._handle_web_tts()
                        return
                    if self.path == "/api/llm-config":
                        self._handle_llm_config_save()
                        return
                    if self.path == "/api/env-profile":
                        self._handle_env_profile_switch()
                        return
                    if self.path == "/api/remote-screen-config":
                        self._handle_remote_screen_config()
                        return
                    if self.path == "/api/session-context/new":
                        self._handle_session_context_new()
                        return
                    if self.path == "/api/session-context/select":
                        self._handle_session_context_select()
                        return
                    if self.path == "/api/session-context/rename":
                        self._handle_session_context_rename()
                        return
                    if self.path == "/api/session-context/clear":
                        self._handle_session_context_clear()
                        return
                    if self.path == "/api/session-context/save":
                        self._handle_session_context_save()
                        return
                    if self.path == "/api/session-context/delete":
                        self._handle_session_context_delete()
                        return
                    self.send_error(404)

                def log_message(self, format: str, *args: Any) -> None:
                    return

                def _send_text(self, value: str, content_type: str, *, send_body: bool = True) -> None:
                    encoded = value.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    if send_body:
                        self.wfile.write(encoded)

                def _send_json(self, value: dict[str, Any]) -> None:
                    encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)

                def _send_json_error(self, status: int, value: dict[str, Any]) -> None:
                    encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)

                def _handle_asset(self, request_path: str, *, send_body: bool = True) -> None:
                    raw_name = request_path.removeprefix("/assets/")
                    asset_name = Path(raw_name).name
                    if not asset_name or asset_name != raw_name:
                        self.send_error(404)
                        return

                    asset_path = Path("assets") / asset_name
                    if not asset_path.is_file():
                        self.send_error(404)
                        return

                    content_type = mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream"
                    try:
                        data = asset_path.read_bytes()
                    except OSError as e:
                        self.send_error(500, f"Could not read asset: {e}")
                        return

                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Cache-Control", "public, max-age=3600")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    if send_body:
                        self.wfile.write(data)

                def _handle_static(self, request_path: str, *, send_body: bool = True) -> None:
                    raw_path = request_path.removeprefix("/static/")
                    parts = [part for part in raw_path.split("/") if part]
                    if not parts or any(part in {".", ".."} for part in parts):
                        self.send_error(404)
                        return

                    static_path = Path("static").joinpath(*parts)
                    try:
                        resolved_root = Path("static").resolve()
                        resolved_path = static_path.resolve()
                    except OSError:
                        self.send_error(404)
                        return
                    if not resolved_path.is_file() or resolved_root not in resolved_path.parents:
                        self.send_error(404)
                        return

                    suffix = resolved_path.suffix.lower()
                    explicit_types = {
                        ".js": "text/javascript; charset=utf-8",
                        ".mjs": "text/javascript; charset=utf-8",
                        ".css": "text/css; charset=utf-8",
                        ".json": "application/json; charset=utf-8",
                        ".wasm": "application/wasm",
                    }
                    content_type = explicit_types.get(suffix) or mimetypes.guess_type(resolved_path.name)[0] or "application/octet-stream"
                    try:
                        data = resolved_path.read_bytes()
                    except OSError as e:
                        self.send_error(500, f"Could not read static file: {e}")
                        return

                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Cache-Control", "public, max-age=3600")
                    self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    if send_body:
                        self.wfile.write(data)

                def _handle_mcp_admin_proxy(
                    self,
                    method: str,
                    request_path: str,
                    query: str,
                    *,
                    send_body: bool = True,
                ) -> None:
                    prefix = "/api/mcp-admin/"
                    raw_tail = request_path.removeprefix(prefix)
                    server_part, _, target_part = raw_tail.partition("/")
                    server_name = unquote(server_part)
                    if not server_name or not target_part:
                        self.send_error(404)
                        return

                    with monitor._lock:
                        target = dict(monitor._mcp_admin_proxy_targets.get(server_name) or {})
                    if not target:
                        self.send_error(404, "MCP admin proxy target is not configured")
                        return

                    target_path = "/" + target_part
                    target_url = f"{target['scheme']}://{target['netloc']}{target_path}"
                    if query:
                        target_url += "?" + query

                    body = None
                    if method == "POST":
                        try:
                            length = int(self.headers.get("Content-Length", "0"))
                        except ValueError:
                            length = 0
                        if length > 2 * 1024 * 1024:
                            self.send_error(413, "MCP admin proxy request is too large")
                            return
                        body = self.rfile.read(length) if length else b""

                    headers: dict[str, str] = {
                        "Accept": self.headers.get("Accept", "*/*"),
                        "User-Agent": "LiveStageAssistant-MCPAdminProxy/1.0",
                    }
                    content_type = self.headers.get("Content-Type")
                    if content_type:
                        headers["Content-Type"] = content_type
                    for key, value in (target.get("headers") or {}).items():
                        headers[str(key)] = str(value)

                    proxy_request = urllib_request.Request(target_url, data=body, headers=headers, method=method)
                    try:
                        with urllib_request.urlopen(proxy_request, timeout=8) as response:
                            data = response.read() if send_body else b""
                            status = response.status
                            reason = response.reason
                            content_type = response.headers.get("Content-Type", "application/octet-stream")
                    except urllib_error.HTTPError as e:
                        data = e.read() if send_body else b""
                        status = e.code
                        reason = e.reason
                        content_type = e.headers.get("Content-Type", "text/plain; charset=utf-8")
                    except (urllib_error.URLError, TimeoutError, OSError) as e:
                        self.send_error(502, f"MCP admin proxy could not reach {server_name}: {e}")
                        return

                    if send_body and "text/html" in content_type.lower():
                        try:
                            text = data.decode("utf-8")
                        except UnicodeDecodeError:
                            text = data.decode("utf-8", errors="replace")
                        proxy_base = f"/api/mcp-admin/{quote(server_name, safe='')}"
                        text = (
                            text.replace('"/mcp', f'"{proxy_base}/mcp')
                            .replace("'/mcp", f"'{proxy_base}/mcp")
                            .replace('"/health', f'"{proxy_base}/health')
                            .replace("'/health", f"'{proxy_base}/health")
                            .replace("</head>", f'<base href="{proxy_base}/">\n</head>')
                        )
                        data = text.encode("utf-8")
                        content_type = "text/html; charset=utf-8"

                    self.send_response(status, reason)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Frame-Options", "SAMEORIGIN")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    if send_body and data:
                        self.wfile.write(data)

                def _handle_vnc_proxy(self, query: str) -> None:
                    target_params = self._parse_vnc_target(query)
                    if target_params is None:
                        return
                    host, port = target_params

                    ws_key = self.headers.get("Sec-WebSocket-Key", "").strip()
                    if self.headers.get("Upgrade", "").lower() != "websocket" or not ws_key:
                        self.send_error(426, "WebSocket upgrade required")
                        return

                    try:
                        target = socket.create_connection((host, port), timeout=5)
                    except OSError as e:
                        self.send_error(502, f"Could not connect to VNC target: {e}")
                        return

                    accept = base64.b64encode(
                        hashlib.sha1((ws_key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
                    ).decode("ascii")
                    self.send_response(101, "Switching Protocols")
                    self.send_header("Upgrade", "websocket")
                    self.send_header("Connection", "Upgrade")
                    self.send_header("Sec-WebSocket-Accept", accept)
                    ws_protocols = [
                        protocol.strip()
                        for protocol in self.headers.get("Sec-WebSocket-Protocol", "").split(",")
                        if protocol.strip()
                    ]
                    if ws_protocols:
                        self.send_header("Sec-WebSocket-Protocol", ws_protocols[0])
                    self.end_headers()
                    self._proxy_websocket_to_tcp(target)

                def _handle_vnc_check(self, query: str) -> None:
                    target_params = self._parse_vnc_target(query)
                    if target_params is None:
                        return
                    host, port = target_params
                    try:
                        with socket.create_connection((host, port), timeout=3):
                            pass
                    except OSError as e:
                        self._send_json({"reachable": False, "host": host, "port": port, "error": str(e)})
                        return
                    self._send_json({"reachable": True, "host": host, "port": port})

                def _parse_vnc_target(self, query: str) -> tuple[str, int] | None:
                    params = parse_qs(query)
                    host = (params.get("host") or [""])[0].strip()
                    port_text = (params.get("port") or ["5900"])[0].strip()
                    if not host:
                        self.send_error(400, "VNC host is required")
                        return None
                    try:
                        port = int(port_text)
                    except ValueError:
                        self.send_error(400, "VNC port must be an integer")
                        return None
                    if port < 1 or port > 65535:
                        self.send_error(400, "VNC port is out of range")
                        return None
                    return host, port

                def _recv_exact(self, sock: socket.socket, length: int) -> bytes:
                    chunks = []
                    remaining = length
                    while remaining > 0:
                        chunk = sock.recv(remaining)
                        if not chunk:
                            raise ConnectionError("socket closed")
                        chunks.append(chunk)
                        remaining -= len(chunk)
                    return b"".join(chunks)

                def _read_ws_frame(self, sock: socket.socket) -> tuple[int, bytes]:
                    header = self._recv_exact(sock, 2)
                    opcode = header[0] & 0x0F
                    masked = bool(header[1] & 0x80)
                    length = header[1] & 0x7F
                    if length == 126:
                        length = struct.unpack("!H", self._recv_exact(sock, 2))[0]
                    elif length == 127:
                        length = struct.unpack("!Q", self._recv_exact(sock, 8))[0]
                    mask = self._recv_exact(sock, 4) if masked else b""
                    payload = self._recv_exact(sock, length) if length else b""
                    if masked and payload:
                        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
                    return opcode, payload

                def _send_ws_frame(self, sock: socket.socket, payload: bytes, opcode: int = 2) -> None:
                    length = len(payload)
                    header = bytearray([0x80 | opcode])
                    if length < 126:
                        header.append(length)
                    elif length <= 0xFFFF:
                        header.append(126)
                        header.extend(struct.pack("!H", length))
                    else:
                        header.append(127)
                        header.extend(struct.pack("!Q", length))
                    sock.sendall(bytes(header) + payload)

                def _proxy_websocket_to_tcp(self, target: socket.socket) -> None:
                    client = self.connection
                    try:
                        client.setblocking(True)
                        target.setblocking(True)
                        while True:
                            readable, _, exceptional = select.select([client, target], [], [client, target], 30)
                            if exceptional:
                                break
                            if not readable:
                                continue
                            if target in readable:
                                data = target.recv(65536)
                                if not data:
                                    break
                                self._send_ws_frame(client, data, opcode=2)
                            if client in readable:
                                opcode, payload = self._read_ws_frame(client)
                                if opcode == 8:
                                    break
                                if opcode == 9:
                                    self._send_ws_frame(client, payload, opcode=10)
                                    continue
                                if opcode in {0, 1, 2} and payload:
                                    target.sendall(payload)
                    except (OSError, ConnectionError):
                        pass
                    finally:
                        try:
                            target.close()
                        except OSError:
                            pass

                def _read_json_body(self, max_bytes: int = 16_384) -> dict[str, Any] | None:
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                    except ValueError:
                        length = 0
                    if length > max_bytes:
                        self.send_error(413, "Request body is too large")
                        return None

                    raw_body = self.rfile.read(length)
                    try:
                        payload = json.loads(raw_body.decode("utf-8") or "{}")
                    except json.JSONDecodeError:
                        self.send_error(400, "Invalid JSON")
                        return None
                    if not isinstance(payload, dict):
                        self.send_error(400, "JSON object required")
                        return None
                    return payload

                def _handle_llm_options(self, query: str) -> None:
                    handler = monitor._llm_options_handler
                    if handler is None:
                        self.send_error(503, "LLM configuration is not available")
                        return

                    provider_values = parse_qs(query).get("provider") or [None]
                    provider = provider_values[0]
                    try:
                        result = handler(provider)
                    except Exception as e:
                        self.send_error(500, f"Could not list LLM options: {e}")
                        return
                    self._send_json(result)

                def _handle_llm_config_save(self) -> None:
                    handler = monitor._llm_config_save_handler
                    if handler is None:
                        self.send_error(503, "LLM configuration is not available")
                        return

                    payload = self._read_json_body(max_bytes=512 * 1024)
                    if payload is None:
                        return

                    provider = str(payload.get("provider") or "").strip().lower()
                    model = str(payload.get("model") or "").strip()
                    if not provider:
                        self.send_error(400, "Provider is required")
                        return
                    cloud_tts_provider = str(payload.get("cloud_tts_provider") or "").strip().lower()
                    tts_output = str(payload.get("tts_output") or "").strip().lower()
                    connectivity_mode = str(payload.get("connectivity_mode") or "").strip().lower()
                    wake_word = str(payload.get("wake_word") or "").strip()
                    stt_prompt = str(payload.get("stt_prompt") or "").strip()
                    system_prompt = str(payload.get("system_prompt") or "").strip()
                    try:
                        session_context_size = int(payload.get("session_context_size") or 0)
                    except (TypeError, ValueError):
                        self.send_error(400, "Session context size must be an integer")
                        return
                    mcp_tool_routing_enabled = bool(payload.get("mcp_tool_routing_enabled"))
                    interrupt_conversation_enabled = bool(payload.get("interrupt_conversation_enabled"))
                    backend_audio_input_device = str(payload.get("backend_audio_input_device") or "").strip()
                    backend_audio_output_device = str(payload.get("backend_audio_output_device") or "").strip()
                    voice_id = str(payload.get("voice_id") or "").strip()
                    thinking_sound_file = str(payload.get("thinking_sound_file") or "").strip()
                    openai_tts_voice = str(payload.get("openai_tts_voice") or "").strip()
                    try:
                        openai_tts_speed = float(payload.get("openai_tts_speed") or 1.0)
                    except (TypeError, ValueError):
                        self.send_error(400, "OpenAI TTS speed must be a number")
                        return

                    try:
                        result = handler(
                            provider,
                            model,
                            cloud_tts_provider,
                            tts_output,
                            connectivity_mode,
                            wake_word,
                            stt_prompt,
                            system_prompt,
                            session_context_size,
                            mcp_tool_routing_enabled,
                            interrupt_conversation_enabled,
                            backend_audio_input_device,
                            backend_audio_output_device,
                            voice_id,
                            thinking_sound_file,
                            openai_tts_voice,
                            openai_tts_speed,
                        )
                    except ValueError as e:
                        self.send_error(400, str(e))
                        return
                    except Exception as e:
                        self.send_error(500, f"Could not save LLM configuration: {e}")
                        return
                    self._send_json(result)

                def _handle_cloud_api_status(self) -> None:
                    handler = monitor._cloud_api_status_handler
                    if handler is None:
                        self.send_error(503, "Cloud API status is not available")
                        return
                    try:
                        result = handler()
                    except Exception as e:
                        self._send_json_error(
                            500,
                            {"ok": False, "error": {"message": f"Could not inspect cloud API status: {e}"}},
                        )
                        return
                    self._send_json(result)

                def _handle_env_profiles(self) -> None:
                    handler = monitor._env_profile_handler
                    if handler is None:
                        self.send_error(503, "Env profile switching is not available")
                        return
                    try:
                        result = handler()
                    except Exception as e:
                        self.send_error(500, f"Could not list env profiles: {e}")
                        return
                    self._send_json(result)

                def _handle_env_profile_switch(self) -> None:
                    handler = monitor._env_profile_switch_handler
                    if handler is None:
                        self.send_error(503, "Env profile switching is not available")
                        return
                    payload = self._read_json_body()
                    if payload is None:
                        return
                    env_file = str(payload.get("env_file") or "").strip()
                    if not env_file:
                        self.send_error(400, "env_file is required")
                        return
                    try:
                        result = handler(env_file)
                    except ValueError as e:
                        self.send_error(400, str(e))
                        return
                    except Exception as e:
                        self.send_error(500, f"Could not switch env profile: {e}")
                        return
                    self._send_json(result)

                def _handle_remote_screen_config(self) -> None:
                    handler = monitor._remote_screen_save_handler
                    if handler is None:
                        self.send_error(503, "Remote screen configuration is not available")
                        return
                    payload = self._read_json_body()
                    if payload is None:
                        return
                    vnc_url = str(payload.get("vnc_url") or "").strip()
                    if not vnc_url:
                        self.send_error(400, "VNC URL is required")
                        return
                    try:
                        result = handler(vnc_url)
                    except ValueError as e:
                        self.send_error(400, str(e))
                        return
                    except Exception as e:
                        self.send_error(500, f"Could not save remote screen configuration: {e}")
                        return
                    self._send_json(result)

                def _handle_session_context_list(self) -> None:
                    handler = monitor._session_context_list_handler
                    if handler is None:
                        self.send_error(503, "Session context is not available")
                        return
                    try:
                        result = handler()
                    except Exception as e:
                        self.send_error(500, f"Could not list session contexts: {e}")
                        return
                    self._send_json(result)

                def _handle_session_context_new(self) -> None:
                    handler = monitor._session_context_new_handler
                    if handler is None:
                        self.send_error(503, "Session context is not available")
                        return
                    payload = self._read_json_body()
                    if payload is None:
                        return
                    title = str(payload.get("title") or "").strip() or None
                    try:
                        result = handler(title)
                    except Exception as e:
                        self.send_error(500, f"Could not create session context: {e}")
                        return
                    self._send_json(result)

                def _handle_session_context_select(self) -> None:
                    handler = monitor._session_context_select_handler
                    if handler is None:
                        self.send_error(503, "Session context is not available")
                        return
                    payload = self._read_json_body()
                    if payload is None:
                        return
                    session_id = str(payload.get("id") or "").strip()
                    if not session_id:
                        self.send_error(400, "Session id is required")
                        return
                    try:
                        result = handler(session_id)
                    except ValueError as e:
                        self.send_error(404, str(e))
                        return
                    except Exception as e:
                        self.send_error(500, f"Could not select session context: {e}")
                        return
                    self._send_json(result)

                def _handle_session_context_rename(self) -> None:
                    handler = monitor._session_context_rename_handler
                    if handler is None:
                        self.send_error(503, "Session context is not available")
                        return
                    payload = self._read_json_body()
                    if payload is None:
                        return
                    session_id = str(payload.get("id") or "").strip()
                    title = str(payload.get("title") or "").strip()
                    if not session_id:
                        self.send_error(400, "Session id is required")
                        return
                    if not title:
                        self.send_error(400, "Session title is required")
                        return
                    try:
                        result = handler(session_id, title)
                    except ValueError as e:
                        self.send_error(404, str(e))
                        return
                    except Exception as e:
                        self.send_error(500, f"Could not rename session context: {e}")
                        return
                    self._send_json(result)

                def _handle_session_context_delete(self) -> None:
                    handler = monitor._session_context_delete_handler
                    if handler is None:
                        self.send_error(503, "Session context is not available")
                        return
                    payload = self._read_json_body()
                    if payload is None:
                        return
                    session_id = str(payload.get("id") or "").strip()
                    if not session_id:
                        self.send_error(400, "Session id is required")
                        return
                    try:
                        result = handler(session_id)
                    except ValueError as e:
                        self.send_error(404, str(e))
                        return
                    except Exception as e:
                        self.send_error(500, f"Could not delete session context: {e}")
                        return
                    self._send_json(result)

                def _handle_session_context_clear(self) -> None:
                    handler = monitor._session_context_clear_handler
                    if handler is None:
                        self.send_error(503, "Session context is not available")
                        return
                    payload = self._read_json_body()
                    if payload is None:
                        return
                    session_id = str(payload.get("id") or "").strip()
                    if not session_id:
                        self.send_error(400, "Session id is required")
                        return
                    try:
                        result = handler(session_id)
                    except ValueError as e:
                        self.send_error(404, str(e))
                        return
                    except Exception as e:
                        self.send_error(500, f"Could not clear session context: {e}")
                        return
                    self._send_json(result)

                def _handle_session_context_save(self) -> None:
                    handler = monitor._session_context_save_handler
                    if handler is None:
                        self.send_error(503, "Session context is not available")
                        return
                    payload = self._read_json_body()
                    if payload is None:
                        return
                    session_id = str(payload.get("id") or "").strip()
                    if not session_id:
                        self.send_error(400, "Session id is required")
                        return
                    try:
                        result = handler(session_id)
                    except ValueError as e:
                        self.send_error(404, str(e))
                        return
                    except Exception as e:
                        self.send_error(500, f"Could not save session context: {e}")
                        return
                    self._send_json(result)

                def _handle_inject_command(self) -> None:
                    payload = self._read_json_body()
                    if payload is None:
                        return

                    command = str(payload.get("command") or "").strip()
                    if not command:
                        self.send_error(400, "Command is required")
                        return

                    monitor.inject_command(command)
                    self._send_json({"accepted": True})

                def _handle_cancel_command(self) -> None:
                    monitor.request_cancel()
                    self._send_json({"accepted": True})

                def _handle_web_transcribe(self) -> None:
                    handler = monitor._web_audio_transcribe_handler
                    if handler is None:
                        self.send_error(503, "Web audio transcription is not available")
                        return

                    payload = self._read_json_body(max_bytes=16 * 1024 * 1024)
                    if payload is None:
                        return

                    audio_base64 = str(payload.get("audio_base64") or "")
                    mime_type = str(payload.get("mime_type") or "audio/webm").strip().lower()
                    if not audio_base64:
                        self.send_error(400, "audio_base64 is required")
                        return

                    try:
                        audio_bytes = base64.b64decode(audio_base64, validate=True)
                    except (binascii.Error, ValueError):
                        self.send_error(400, "audio_base64 is invalid")
                        return

                    apply_wake_word_gate = bool(payload.get("apply_wake_word"))

                    try:
                        result = handler(audio_bytes, mime_type, apply_wake_word_gate)
                    except ValueError as e:
                        self.send_error(400, str(e))
                        return
                    except Exception as e:
                        self.send_error(500, f"Could not transcribe web audio: {e}")
                        return
                    self._send_json(result)

                def _handle_web_tts(self) -> None:
                    handler = monitor._web_audio_tts_handler
                    if handler is None:
                        self.send_error(503, "Web audio TTS is not available")
                        return

                    payload = self._read_json_body()
                    if payload is None:
                        return

                    text = str(payload.get("text") or "").strip()
                    if not text:
                        self.send_error(400, "text is required")
                        return

                    try:
                        result = handler(text)
                    except ValueError as e:
                        error = concise_web_tts_error(e)
                        self._send_json_error(400, {"ok": False, "error": error})
                        return
                    except Exception as e:
                        error = concise_web_tts_error(e)
                        if error.get("kind") in {"quota", "billing"}:
                            status = 402
                        elif error.get("kind") == "auth":
                            status = 401
                        elif error.get("kind") == "rate_limit":
                            status = 429
                        else:
                            status = 500
                        self._send_json_error(status, {"ok": False, "error": error})
                        return
                    self._send_json(result)

            self._server = ThreadingHTTPServer((host, port), MonitorHandler)
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                name="voice-assistant-web-monitor",
                daemon=True,
            )
            self._thread.start()
            return self._server.server_address

    def stop(self) -> None:
        with self._lock:
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None

        if server:
            server.shutdown()
            server.server_close()
        if thread:
            thread.join(timeout=2)

    def append_log(self, value: str, source: str = "stdout") -> None:
        if not value:
            return

        filtered_value = self._filter_log_value(value)
        if not filtered_value:
            return

        self._append_filtered_log(filtered_value)

    def write_console(self, value: str, original: TextIO, source: str = "stdout") -> None:
        if not value:
            return

        filtered_value = self._filter_log_value(value)
        if not filtered_value:
            return

        original.write(filtered_value)
        self._append_filtered_log(filtered_value)

    def _append_filtered_log(self, filtered_value: str) -> None:
        with self._lock:
            self._log_chunks.append(filtered_value)
            self._log_chars += len(filtered_value)
            while self._log_chars > self.max_log_chars and self._log_chunks:
                self._log_chars -= len(self._log_chunks.popleft())
            self._snapshot["updated_at"] = time.time()

    def inject_command(self, command: str) -> None:
        cleaned_command = command.strip()
        if not cleaned_command:
            return

        with self._lock:
            self._injected_commands.append(cleaned_command)
            self._snapshot["updated_at"] = time.time()

    def pop_injected_command(self) -> str | None:
        with self._lock:
            if not self._injected_commands:
                return None
            self._snapshot["updated_at"] = time.time()
            return self._injected_commands.popleft()

    def request_cancel(self) -> None:
        handler = None
        with self._lock:
            if self._snapshot.get("assistant_busy"):
                self._cancel_requested = True
            handler = self._cancel_handler
            self._snapshot["updated_at"] = time.time()
        if handler:
            handler()

    def pop_cancel_requested(self) -> bool:
        with self._lock:
            if not self._cancel_requested:
                return False
            self._cancel_requested = False
            self._snapshot["updated_at"] = time.time()
            return True

    def append_dialogue(self, role: str, text: str, *, speak: bool = False) -> None:
        cleaned_text = text.strip()
        if not cleaned_text:
            return

        normalized_role = role if role in {"user", "assistant"} else "assistant"
        with self._lock:
            self._messages.append(
                    {
                        "id": self._next_message_id,
                        "role": normalized_role,
                        "text": cleaned_text,
                        "speak": bool(speak),
                        "created_at": time.time(),
                    }
            )
            self._next_message_id += 1
            while len(self._messages) > self.max_messages:
                self._messages.popleft()
            self._snapshot["updated_at"] = time.time()

    def replace_dialogue(self, messages: list[dict[str, Any]]) -> None:
        with self._lock:
            self._messages.clear()
            next_id = 1
            for message in messages[-self.max_messages :]:
                text = str(message.get("text") or "").strip()
                if not text:
                    continue
                role = message.get("role") if message.get("role") in {"user", "assistant"} else "assistant"
                message_id = int(message.get("id") or next_id)
                self._messages.append(
                    {
                        "id": message_id,
                        "role": role,
                        "text": text,
                        "speak": bool(message.get("speak")),
                        "created_at": float(message.get("created_at") or time.time()),
                    }
                )
                next_id = max(next_id, message_id + 1)
            self._next_message_id = next_id
            self._snapshot["updated_at"] = time.time()

    def set_context_state(
        self,
        session_context: dict[str, Any],
        *,
        session_context_size: int | None = None,
    ) -> None:
        with self._lock:
            self._snapshot["session_context"] = session_context
            if session_context_size is not None:
                self._snapshot["session_context_size"] = session_context_size
            self._snapshot["updated_at"] = time.time()

    def set_assistant_busy(self, busy: bool) -> None:
        with self._lock:
            self._snapshot["assistant_busy"] = busy
            if not busy:
                self._cancel_requested = False
            self._snapshot["updated_at"] = time.time()

    def set_environment_loading(self, active: bool, title: str = "rafraichissement de l'environnement") -> None:
        with self._lock:
            self._snapshot["environment_loading"] = {
                "active": bool(active),
                "title": title if active else "",
            }
            self._snapshot["updated_at"] = time.time()

    def _filter_log_value(self, value: str) -> str:
        return value

    def update(
        self,
        *,
        mode: str | None = None,
        env_file: str | Path | None = None,
        internet: str | bool | None = None,
        services: dict[str, dict[str, str]] | None = None,
        env_values: dict[str, Any] | None = None,
        mcp_config: dict[str, Any] | None = None,
        prompt: str | None = None,
        web_audio: dict[str, Any] | None = None,
        remote_screen: dict[str, Any] | None = None,
        thinking_sound_file: str | None = None,
    ) -> None:
        with self._lock:
            if mode is not None:
                self._snapshot["mode"] = mode
            if env_file is not None:
                self._snapshot["env_file"] = str(env_file)
            if internet is not None:
                if isinstance(internet, bool):
                    self._snapshot["internet"] = "online" if internet else "offline"
                else:
                    self._snapshot["internet"] = internet
            if services is not None:
                merged_services = dict(self._snapshot.get("services") or {})
                merged_services.update(services)
                self._snapshot["services"] = merged_services
            if env_values is not None or mcp_config is not None:
                config = dict(self._snapshot.get("config") or {})
                if env_values is not None:
                    config["env"] = redact_mapping(env_values)
                if mcp_config is not None:
                    config["mcp"] = redact_mapping(mcp_config)
                    self._snapshot["mcp_servers"] = build_mcp_server_admin_frames(mcp_config)
                    self._mcp_admin_proxy_targets = build_mcp_admin_proxy_targets(mcp_config)
                self._snapshot["config"] = config
                self._snapshot["config_text"] = json.dumps(config, ensure_ascii=False, indent=2)
            if prompt is not None:
                self._snapshot["prompt"] = prompt
            if web_audio is not None:
                self._snapshot["web_audio"] = web_audio
            if remote_screen is not None:
                self._snapshot["remote_screen"] = remote_screen
            if thinking_sound_file is not None:
                cleaned_file = Path(thinking_sound_file).name if thinking_sound_file else ""
                if cleaned_file:
                    self._snapshot["thinking_sound_url"] = f"/assets/{cleaned_file}"
                else:
                    self._snapshot["thinking_sound_url"] = ""
            self._snapshot["updated_at"] = time.time()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            snapshot = dict(self._snapshot)
            snapshot["logs"] = "".join(self._log_chunks)
            snapshot["messages"] = list(self._messages)
            snapshot["uptime_seconds"] = int(time.time() - self._started_at)
            return snapshot


def build_service_state(
    *,
    llm_provider: str,
    model: str,
    stt_provider: str,
    tts_provider: str,
    mcp_config: dict[str, Any] | None,
    mcp_status: str = "configured",
) -> dict[str, dict[str, str]]:
    server_names = sorted((mcp_config or {}).get("mcpServers", {}).keys())
    mcp_detail = ", ".join(server_names) if server_names else "no configured servers"
    return {
        "LLM": {"status": "configured", "detail": f"{llm_provider} / {model}"},
        "STT": {"status": "configured", "detail": stt_provider},
        "TTS": {"status": "configured", "detail": tts_provider},
        "MCP": {"status": mcp_status, "detail": mcp_detail},
    }


VNC_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Live Stage Assistant noVNC</title>
  <style>
    html, body, #screen {
      width: 100%;
      height: 100%;
      margin: 0;
      overflow: hidden;
      background: #111;
      color: #eee;
      font: 13px/1.4 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    #status {
      position: fixed;
      top: 10px;
      left: 10px;
      z-index: 2;
      max-width: calc(100% - 20px);
      border: 1px solid rgba(255, 255, 255, 0.18);
      border-radius: 8px;
      padding: 8px 10px;
      background: rgba(0, 0, 0, 0.68);
      color: #f2f2f2;
    }
    #screen.connected + #status {
      opacity: 0.18;
    }
    #screen.connected + #status:hover {
      opacity: 1;
    }
  </style>
</head>
<body>
  <div id="screen"></div>
  <div id="status">Connexion noVNC...</div>
  <script type="module">
    const statusEl = document.querySelector("#status");
    const screenEl = document.querySelector("#screen");
    const params = new URLSearchParams(window.location.search);

    function setStatus(text, connected = false) {
      statusEl.textContent = text;
      screenEl.classList.toggle("connected", connected);
      window.parent.postMessage({ type: "lsa-vnc-status", text, connected }, window.location.origin);
    }

    try {
      const host = params.get("host") || "192.168.0.160";
      const port = params.get("port") || "5900";
      const password = params.get("password") || "ronron";
      const scheme = window.location.protocol === "https:" ? "wss" : "ws";
      const proxyUrl = `${scheme}://${window.location.host}/api/vnc-proxy?host=${encodeURIComponent(host)}&port=${encodeURIComponent(port)}`;
      const checkUrl = `/api/vnc-check?host=${encodeURIComponent(host)}&port=${encodeURIComponent(port)}`;

      const { default: RFB } = await import("/static/novnc/core/rfb.js?v=lsa-novnc-20260602-1");
      setStatus(`Test VNC ${host}:${port}...`, false);
      const checkResponse = await fetch(checkUrl, { cache: "no-store" });
      if (!checkResponse.ok) {
        throw new Error(`diagnostic VNC HTTP ${checkResponse.status}`);
      }
      const check = await checkResponse.json();
      if (!check.reachable) {
        setStatus(`hors ligne cible VNC injoignable: ${check.error || "connexion impossible"}`, false);
        throw new Error("LSA_VNC_TARGET_UNREACHABLE");
      }
      setStatus(`Connexion WebSocket VNC ${host}:${port}...`, false);
      console.info("[LSA noVNC] TCP target reachable, opening WebSocket proxy", { host, port, proxyUrl });
      const rfb = new RFB(screenEl, proxyUrl, { credentials: { password } });
      rfb.viewOnly = false;
      rfb.scaleViewport = true;
      rfb.resizeSession = false;
      console.info("[LSA noVNC] RFB instance created");
      rfb.addEventListener("connect", () => {
        console.info("[LSA noVNC] connected");
        setStatus(`Connecté à ${host}:${port}`, true);
      });
      rfb.addEventListener("disconnect", (event) => {
        console.info("[LSA noVNC] disconnected", event.detail || {});
        const detail = event.detail && event.detail.clean ? "" : " connexion interrompue";
        setStatus(`hors ligne${detail}`, false);
      });
      rfb.addEventListener("credentialsrequired", () => {
        console.info("[LSA noVNC] credentials required");
        setStatus("Mot de passe VNC requis ou invalide", false);
      });
      rfb.addEventListener("securityfailure", () => {
        console.info("[LSA noVNC] security failure");
        setStatus("Échec sécurité VNC", false);
      });
    } catch (error) {
      if (error && error.message === "LSA_VNC_TARGET_UNREACHABLE") {
        console.info("[LSA noVNC] TCP target unreachable");
      } else {
      setStatus(`noVNC indisponible: ${error}`, false);
      }
    }
  </script>
</body>
</html>
"""


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Live Stage Assistant</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #f7f7f8;
      --surface: #ffffff;
      --surface-soft: #f1f1f3;
      --text: #1f2328;
      --muted: #697179;
      --border: #d7dce0;
      --user: #1f2328;
      --user-text: #ffffff;
      --assistant: #ffffff;
      --accent: #15803d;
      --ok: #1f9d55;
      --warn: #c77900;
      --bad: #c73b3b;
      --idle: #8a9499;
      --shadow: 0 18px 50px rgba(24, 28, 32, 0.16);
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #181a1d;
        --surface: #202327;
        --surface-soft: #2a2e33;
        --text: #edf0f2;
        --muted: #a8b0b7;
        --border: #3a4148;
        --user: #eceff2;
        --user-text: #17191b;
        --assistant: #202327;
        --shadow: 0 18px 60px rgba(0, 0, 0, 0.42);
      }
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; }
    body {
      margin: 0;
      overflow: hidden;
      font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    button, textarea, input, select { font: inherit; }
    button { cursor: pointer; }
    button:disabled {
      opacity: 0.55;
      cursor: default;
    }
    .app-shell {
      height: 100%;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
    }
    .main-layout {
      min-height: 0;
      display: grid;
      grid-template-columns: 240px minmax(0, 1fr);
    }
    .session-sidebar {
      min-height: 0;
      overflow-y: auto;
      border-right: 1px solid var(--border);
      background: var(--surface-soft);
      padding: 12px;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      gap: 10px;
    }
    .session-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      font-weight: 650;
    }
    .session-list {
      min-height: 0;
      overflow-y: auto;
      display: grid;
      align-content: start;
      gap: 6px;
    }
    .session-row {
      position: relative;
      width: 100%;
      min-height: 42px;
      border: 1px solid transparent;
      border-radius: 8px;
      background: transparent;
      color: var(--text);
      display: grid;
      grid-template-columns: minmax(0, 1fr) 32px;
      align-items: stretch;
    }
    .session-row.active {
      border-color: var(--border);
      background: var(--surface);
    }
    .session-main {
      min-width: 0;
      border: 0;
      padding: 8px 9px;
      background: transparent;
      color: var(--text);
      text-align: left;
      display: grid;
      gap: 2px;
    }
    .session-menu-button {
      width: 32px;
      border: 0;
      border-radius: 0 8px 8px 0;
      background: transparent;
      color: var(--muted);
      font-weight: 700;
      letter-spacing: 0;
    }
    .session-summary-button {
      width: 32px;
      border: 0;
      border-radius: 0 8px 8px 0;
      background: transparent;
      color: var(--accent);
      font-weight: 700;
      display: none;
    }
    .session-menu-button:hover,
    .session-summary-button:hover,
    .session-main:hover {
      background: color-mix(in srgb, var(--surface) 62%, transparent);
    }
    .session-summary-popover {
      position: fixed;
      z-index: 35;
      width: min(360px, calc(100vw - 24px));
      max-height: min(320px, calc(100vh - 24px));
      overflow: auto;
      display: none;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 11px 12px;
      background: var(--surface);
      color: var(--text);
      box-shadow: var(--shadow);
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-size: 12px;
      line-height: 1.45;
    }
    .session-summary-popover.open {
      display: block;
    }
    .session-menu {
      position: absolute;
      top: 34px;
      right: 4px;
      z-index: 8;
      min-width: 128px;
      display: none;
      padding: 4px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--surface);
      box-shadow: var(--shadow);
    }
    .session-row.menu-open .session-menu {
      display: grid;
    }
    .session-menu-action {
      min-height: 30px;
      border: 0;
      border-radius: 6px;
      padding: 0 8px;
      background: transparent;
      color: var(--text);
      text-align: left;
    }
    .session-menu-action:hover {
      background: var(--surface-soft);
    }
    .session-menu-action.danger {
      color: var(--bad);
    }
    .session-title {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-weight: 650;
    }
    .session-meta {
      color: var(--muted);
      font-size: 11px;
    }
    .topbar {
      min-height: 58px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      padding: 10px 18px;
      border-bottom: 1px solid transparent;
    }
    .brand {
      min-width: 0;
      display: grid;
      gap: 2px;
    }
    h1 {
      margin: 0;
      font-size: 16px;
      font-weight: 650;
      letter-spacing: 0;
    }
    .meta {
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }
    .meta.error {
      color: var(--bad);
      font-weight: 650;
    }
    .icon-button {
      width: 38px;
      height: 38px;
      min-height: 38px;
      border: 1px solid var(--border);
      border-radius: 8px;
      display: grid;
      place-items: center;
      background: var(--surface);
      color: var(--text);
      font-size: 18px;
      line-height: 1;
    }
    .icon-button:hover {
      background: var(--surface-soft);
    }
    .small-button {
      min-height: 32px;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 0 10px;
      background: var(--surface);
      color: var(--text);
      font-weight: 650;
    }
    .chat-scroll {
      padding: 22px 16px 16px;
      scroll-behavior: smooth;
    }
    .chat-panel {
      min-height: 0;
      overflow-y: auto;
      scroll-behavior: smooth;
    }
    .vnc-panel {
      margin: 10px 16px 0;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--surface);
      overflow: hidden;
    }
    .vnc-panel summary {
      display: flex;
      align-items: center;
      gap: 10px;
      border-bottom: 0;
    }
    .vnc-panel summary::before {
      content: "▸";
      color: var(--muted);
      font-size: 12px;
      transform: translateY(-1px);
    }
    .vnc-panel[open] summary::before {
      content: "▾";
    }
    .vnc-status {
      margin-left: auto;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    .vnc-status.online { color: var(--ok); }
    .vnc-status.offline { color: var(--bad); }
    .vnc-controls {
      display: grid;
      grid-template-columns: minmax(220px, 1fr) auto;
      gap: 10px;
      padding: 12px;
      border-top: 1px solid var(--border);
    }
    .vnc-frame-wrap {
      width: min(1368px, calc(100vw - 300px));
      height: 768px;
      min-height: 360px;
      max-height: min(768px, calc(100vh - 230px));
      margin: 0 auto 12px;
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
      background: var(--surface-soft);
    }
    .vnc-frame {
      width: 100%;
      height: 100%;
      border: 0;
      display: block;
      background: #000;
    }
    .messages {
      width: min(920px, 100%);
      min-height: 100%;
      margin: 0 auto;
      display: flex;
      flex-direction: column;
      justify-content: flex-end;
      gap: 18px;
    }
    .empty-state {
      align-self: center;
      margin: auto 0;
      color: var(--muted);
      font-size: 15px;
    }
    .message-row {
      width: 100%;
      display: flex;
    }
    .message-row.user {
      justify-content: flex-end;
    }
    .message-row.assistant {
      justify-content: flex-start;
    }
    .bubble {
      max-width: min(74%, 720px);
      min-width: 96px;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 13px 15px;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      box-shadow: 0 1px 0 rgba(0, 0, 0, 0.03);
    }
    .message-row.user .bubble {
      background: var(--user);
      color: var(--user-text);
      border-color: var(--user);
    }
    .message-row.assistant .bubble {
      background: var(--assistant);
      color: var(--text);
    }
    .message-row.context-included .bubble {
      border-color: var(--accent);
      box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 16%, transparent);
    }
    .message-row.user.context-included .bubble {
      background: var(--accent);
      color: #ffffff;
    }
    .message-row.assistant.context-included .bubble {
      background: color-mix(in srgb, var(--accent) 15%, var(--assistant));
    }
    .message-row.pending .bubble {
      opacity: 0.74;
    }
    .thinking-bubble {
      min-width: 72px;
      display: inline-flex;
      align-items: center;
      gap: 5px;
    }
    .thinking-dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--muted);
      opacity: 0.45;
      animation: thinkingPulse 1.2s infinite ease-in-out;
    }
    .thinking-dot:nth-child(2) {
      animation-delay: 0.16s;
    }
    .thinking-dot:nth-child(3) {
      animation-delay: 0.32s;
    }
    @keyframes thinkingPulse {
      0%, 80%, 100% {
        transform: translateY(0);
        opacity: 0.35;
      }
      40% {
        transform: translateY(-4px);
        opacity: 0.9;
      }
    }
    .composer-wrap {
      padding: 12px 16px 18px;
      background: linear-gradient(to top, var(--bg) 78%, rgba(247, 247, 248, 0));
    }
    @media (prefers-color-scheme: dark) {
      .composer-wrap {
        background: linear-gradient(to top, var(--bg) 78%, rgba(24, 26, 29, 0));
      }
    }
    .inject-form {
      width: min(920px, 100%);
      min-height: 56px;
      margin: 0 auto;
      display: grid;
      grid-template-columns: 40px 40px minmax(0, 1fr);
      gap: 8px;
      align-items: end;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 8px;
      background: var(--surface);
      box-shadow: 0 8px 32px rgba(0, 0, 0, 0.08);
    }
    .inject-form.busy {
      grid-template-columns: 40px 40px minmax(0, 1fr) 40px;
    }
    .command-field {
      position: relative;
      min-width: 0;
      min-height: 38px;
    }
    #soundwave {
      position: absolute;
      inset: 0;
      z-index: 0;
      width: 100%;
      height: 100%;
      border-radius: 8px;
      opacity: 0;
      pointer-events: none;
      transition: opacity 0.16s ease;
    }
    .command-field.soundwave-active #soundwave {
      opacity: 1;
    }
    #inject-command {
      position: relative;
      z-index: 1;
      width: 100%;
      max-height: 160px;
      min-height: 38px;
      resize: none;
      border: 0;
      outline: none;
      padding: 9px 8px;
      background: transparent;
      color: var(--text);
      line-height: 1.4;
    }
    .command-field.soundwave-active #inject-command {
      color: transparent;
      caret-color: transparent;
    }
    .command-field.soundwave-active #inject-command::placeholder {
      color: transparent;
    }
    #inject-command:disabled {
      color: var(--muted);
      cursor: not-allowed;
    }
    #web-mic,
    #web-conversation,
    #inject-stop {
      width: 40px;
      height: 40px;
      min-height: 40px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--surface-soft);
      color: var(--text);
      font-size: 17px;
      line-height: 1;
    }
    #inject-stop {
      display: none;
      background: var(--surface-soft);
      color: #000000;
      font-size: 13px;
    }
    #inject-stop.visible {
      display: block;
    }
    #web-conversation.active {
      border-color: var(--accent);
      color: var(--accent);
      background: color-mix(in srgb, var(--accent) 12%, var(--surface-soft));
    }
    #web-mic.recording {
      border-color: var(--bad);
      color: var(--bad);
    }
    #web-mic:disabled,
    #web-conversation:disabled,
    #inject-stop:disabled {
      color: var(--muted);
      cursor: not-allowed;
      opacity: 0.55;
    }
    .overlay {
      position: fixed;
      inset: 0;
      z-index: 20;
      display: none;
      background: rgba(0, 0, 0, 0.34);
      padding: 18px;
    }
    .overlay.open {
      display: grid;
      place-items: center;
    }
    .loading-overlay {
      position: fixed;
      inset: 0;
      z-index: 40;
      display: none;
      place-items: center;
      background: rgba(0, 0, 0, 0.26);
      backdrop-filter: blur(2px);
    }
    .loading-overlay.open {
      display: grid;
    }
    .loading-panel {
      min-width: min(320px, calc(100vw - 36px));
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 18px;
      background: var(--surface);
      color: var(--text);
      box-shadow: var(--shadow);
      display: grid;
      grid-template-columns: 28px minmax(0, 1fr);
      gap: 12px;
      align-items: center;
    }
    .loading-spinner {
      width: 24px;
      height: 24px;
      border: 3px solid color-mix(in srgb, var(--accent) 22%, var(--border));
      border-top-color: var(--accent);
      border-radius: 50%;
      animation: loadingSpin 0.8s linear infinite;
    }
    .loading-title {
      font-weight: 650;
    }
    .loading-detail {
      color: var(--muted);
      font-size: 12px;
      margin-top: 2px;
    }
    @keyframes loadingSpin {
      to { transform: rotate(360deg); }
    }
    .settings-panel {
      width: min(1040px, 100%);
      height: min(840px, 100%);
      min-height: 420px;
      display: grid;
      grid-template-rows: auto auto minmax(0, 1fr);
      overflow: hidden;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--surface);
      box-shadow: var(--shadow);
    }
    .settings-header {
      min-height: 58px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 14px 10px 18px;
      border-bottom: 1px solid var(--border);
    }
    .settings-title {
      font-size: 16px;
      font-weight: 650;
    }
    .tabs {
      display: flex;
      gap: 8px;
      padding: 10px 14px;
      border-bottom: 1px solid var(--border);
      background: var(--surface-soft);
    }
    .tab {
      min-height: 36px;
      border: 1px solid transparent;
      border-radius: 8px;
      padding: 0 12px;
      background: transparent;
      color: var(--muted);
      font-weight: 650;
    }
    .tab.active {
      border-color: var(--border);
      background: var(--surface);
      color: var(--text);
    }
    .tab-panel {
      min-height: 0;
      overflow-y: auto;
      padding: 14px;
      display: none;
      gap: 12px;
    }
    .tab-panel.active {
      display: flex;
      flex-direction: column;
    }
    section {
      flex: 0 0 auto;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--surface);
      overflow: hidden;
    }
    summary {
      cursor: pointer;
      padding: 12px 14px;
      font-weight: 650;
      border-bottom: 1px solid var(--border);
    }
    details:not([open]) summary { border-bottom: 0; }
    .state {
      padding: 14px;
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
      gap: 10px;
    }
    .tile {
      min-height: 74px;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 11px;
      display: grid;
      gap: 4px;
      background: var(--surface);
    }
    .tile-title {
      display: flex;
      align-items: center;
      gap: 8px;
      font-weight: 650;
    }
    .led {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--idle);
      box-shadow: 0 0 0 3px color-mix(in srgb, currentColor 12%, transparent);
      flex: 0 0 auto;
    }
    .ok { background: var(--ok); }
    .warn { background: var(--warn); }
    .bad { background: var(--bad); }
    .idle { background: var(--idle); }
    .detail {
      color: var(--muted);
      overflow-wrap: anywhere;
    }
    .config-controls {
      display: grid;
      grid-template-columns: minmax(150px, 220px) minmax(200px, 1fr) auto;
      gap: 10px;
      align-items: end;
      padding: 14px;
    }
    .field {
      min-width: 0;
      display: grid;
      gap: 5px;
    }
    .field.full-row {
      grid-column: 1 / -1;
    }
	    .field.hidden {
	      display: none;
	    }
    .hidden {
      display: none !important;
    }
    .inline-badge {
      display: inline-flex;
      align-items: center;
      min-height: 18px;
      margin-left: 6px;
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 0 7px;
      background: var(--surface-soft);
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      vertical-align: middle;
    }
    .offline-audio-summary {
      grid-column: 1 / -1;
      min-height: 38px;
      display: flex;
      align-items: center;
      padding: 0 12px;
      border: 1px solid var(--border);
      background: var(--surface-soft);
      color: var(--muted);
      font-size: 13px;
    }
    .offline-audio-summary.hidden {
      display: none;
    }
    .cloud-api-panel {
      padding: 14px;
      display: grid;
      gap: 10px;
    }
    .mcp-server-panel {
      padding: 14px;
      display: grid;
      gap: 12px;
    }
    .mcp-server-toolbar {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(210px, 260px);
      gap: 12px;
      align-items: center;
    }
    .mcp-server-grid {
      display: grid;
      gap: 12px;
    }
    .mcp-server-card {
      min-width: 0;
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
      background: var(--surface);
    }
    .mcp-server-head {
      min-height: 42px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 10px 12px;
      border-bottom: 1px solid var(--border);
      background: var(--surface-soft);
    }
    .mcp-server-title {
      min-width: 0;
      display: grid;
      gap: 2px;
    }
    .mcp-server-name {
      color: var(--text);
      font-weight: 700;
      overflow-wrap: anywhere;
    }
    .mcp-server-url {
      color: var(--muted);
      font: 12px/1.35 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      overflow-wrap: anywhere;
    }
    .mcp-server-actions {
      flex: 0 0 auto;
      display: flex;
      gap: 8px;
      align-items: center;
    }
    .mcp-server-open {
      min-height: 30px;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 6px 10px;
      color: var(--accent);
      background: var(--surface);
      font-size: 12px;
      font-weight: 700;
      text-decoration: none;
    }
    .mcp-server-load {
      min-height: 30px;
      border: 1px solid var(--accent);
      border-radius: 8px;
      padding: 6px 10px;
      color: #fff;
      background: var(--accent);
      font-size: 12px;
      font-weight: 700;
      cursor: pointer;
    }
    .mcp-server-frame {
      display: block;
      width: 100%;
      height: min(62vh, 620px);
      border: 0;
      background: var(--surface-soft);
    }
    .mcp-server-empty {
      padding: 12px;
      border: 1px dashed var(--border);
      border-radius: 8px;
      color: var(--muted);
      background: var(--surface-soft);
    }
    .mcp-server-placeholder {
      display: grid;
      gap: 8px;
      padding: 12px;
      color: var(--muted);
      background: var(--surface);
    }
    .cloud-api-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
    }
    .cloud-api-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .cloud-api-card {
      min-width: 0;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px;
      background: var(--surface);
      display: grid;
      gap: 8px;
    }
    .cloud-api-title {
      display: flex;
      justify-content: space-between;
      gap: 8px;
      font-weight: 700;
    }
    .cloud-api-status {
      color: var(--muted);
      font-size: 12px;
    }
    .cloud-api-status.ok { color: var(--ok); }
    .cloud-api-status.warn { color: var(--warn); }
    .cloud-api-status.bad { color: var(--bad); }
    .cloud-api-line {
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }
    .cloud-api-key {
      color: var(--text);
      font: 12px/1.35 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      overflow-wrap: anywhere;
    }
    .segmented {
      min-height: 38px;
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
      background: var(--surface-soft);
    }
    .segmented label {
      min-width: 0;
      display: grid;
      place-items: center;
      padding: 0 8px;
      color: var(--text);
      font-size: 12px;
      font-weight: 650;
      border-right: 1px solid var(--border);
      cursor: pointer;
    }
    .segmented label:last-child {
      border-right: 0;
    }
    .segmented input {
      position: absolute;
      width: auto;
      min-height: 0;
      margin: 0;
      opacity: 0;
      pointer-events: none;
    }
    .segmented label:has(input:checked) {
      background: var(--accent);
      color: #fff;
    }
    .segmented label:has(input:disabled) {
      opacity: 0.45;
      cursor: default;
    }
    #connectivity-mode {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    #mcp-admin-route {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    label {
      font-size: 12px;
      color: var(--muted);
      font-weight: 650;
    }
    input, select {
      width: 100%;
      min-height: 38px;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 0 10px;
      background: var(--surface-soft);
      color: var(--text);
      outline: none;
    }
    input:focus, select:focus {
      border-color: var(--accent);
    }
    #llm-save {
      min-height: 38px;
      border: 1px solid var(--accent);
      border-radius: 8px;
      padding: 0 14px;
      background: var(--accent);
      color: white;
      font-weight: 650;
    }
    .config-message {
      min-height: 18px;
      color: var(--muted);
      overflow-wrap: anywhere;
    }
    .config-footer {
      flex: 0 0 auto;
      display: flex;
      justify-content: flex-end;
      align-items: center;
      gap: 12px;
      padding: 10px 14px 14px;
    }
    .prompt-fields {
      display: grid;
      gap: 14px;
      padding: 14px;
    }
    textarea.inspect {
      display: block;
      width: 100%;
      min-height: 220px;
      resize: vertical;
      border: 0;
      border-top: 1px solid var(--border);
      padding: 12px;
      background: var(--surface);
      color: var(--text);
      font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      outline: none;
    }
    textarea.inspect[readonly] {
      background: var(--surface-soft);
    }
    #logs { min-height: 360px; }
    #stt-prompt { min-height: 96px; }
    #assistant-system-prompt { min-height: 180px; }
    #prompt { min-height: 300px; }
    @media (max-width: 720px) {
      .topbar { padding-inline: 12px; }
      .main-layout { grid-template-columns: 1fr; }
      .session-sidebar {
        max-height: 140px;
        border-right: 0;
        border-bottom: 1px solid var(--border);
      }
      .vnc-frame-wrap {
        width: calc(100vw - 32px);
        height: min(62vh, 768px);
      }
      .vnc-controls { grid-template-columns: 1fr; }
      .bubble { max-width: 88%; }
      .overlay { padding: 8px; }
      .settings-panel { height: 100%; }
      .config-controls { grid-template-columns: 1fr; }
      .cloud-api-grid { grid-template-columns: 1fr; }
      .mcp-server-toolbar { grid-template-columns: 1fr; }
      .mcp-server-head { align-items: stretch; flex-direction: column; }
      .mcp-server-actions { justify-content: space-between; }
      .mcp-server-frame { height: min(70vh, 560px); }
    }
    @media (hover: none), (pointer: coarse) {
      .session-row.has-summary {
        grid-template-columns: minmax(0, 1fr) 32px 32px;
      }
      .session-row.has-summary .session-menu-button {
        border-radius: 0;
      }
      .session-row.has-summary .session-summary-button {
        display: block;
      }
    }
  </style>
</head>
<body>
  <div class="app-shell">
    <header class="topbar">
      <div class="brand">
        <h1>Live Stage Assistant</h1>
        <div class="meta" id="meta">connecting...</div>
      </div>
      <button class="icon-button" id="settings-open" type="button" title="Settings" aria-label="Settings">&#9881;</button>
    </header>

    <main class="main-layout">
      <aside class="session-sidebar" aria-label="Sessions">
        <div class="session-header">
          <span>Sessions</span>
          <button class="small-button" id="session-new" type="button" title="New session" aria-label="New session">+</button>
        </div>
        <div class="session-list" id="session-list"></div>
      </aside>
      <div class="chat-panel" id="chat-panel">
        <details class="vnc-panel" id="vnc-panel">
          <summary>Remote screen <span class="vnc-status offline" id="vnc-status">hors ligne</span></summary>
          <div class="vnc-controls">
            <input id="vnc-url" type="text" value="vnc://192.168.0.160:5900?password=ronron" spellcheck="false" aria-label="VNC URL">
            <button class="small-button" id="vnc-connect" type="button">Connecter</button>
          </div>
          <div class="vnc-frame-wrap">
            <iframe class="vnc-frame" id="vnc-frame" title="noVNC remote screen" loading="lazy" referrerpolicy="no-referrer"></iframe>
          </div>
        </details>
        <div class="chat-scroll" id="chat-scroll">
          <div class="messages" id="messages"></div>
        </div>
      </div>
    </main>

    <div class="composer-wrap">
      <form class="inject-form" id="inject-form">
        <button id="web-conversation" type="button" title="Conversation mode" aria-label="Conversation mode" disabled>💬</button>
        <button id="web-mic" type="button" title="Voice input" aria-label="Voice input" disabled>🎙️</button>
        <div class="command-field" id="command-field">
          <canvas id="soundwave" aria-hidden="true"></canvas>
          <textarea id="inject-command" rows="1" autocomplete="off" enterkeyhint="send" placeholder="Message"></textarea>
        </div>
        <button id="inject-stop" type="button" title="Stop" aria-label="Stop">&#9632;</button>
      </form>
    </div>
  </div>

  <div class="overlay" id="settings-overlay" aria-hidden="true">
    <div class="settings-panel" role="dialog" aria-modal="true" aria-labelledby="settings-title">
      <div class="settings-header">
        <div class="settings-title" id="settings-title">Settings</div>
        <button class="icon-button" id="settings-close" type="button" title="Close" aria-label="Close">&times;</button>
      </div>
      <div class="tabs" role="tablist">
        <button class="tab active" id="tab-monitor" type="button" role="tab" aria-selected="true" aria-controls="panel-monitor">Monitor</button>
        <button class="tab" id="tab-config" type="button" role="tab" aria-selected="false" aria-controls="panel-config">Config</button>
      </div>
      <div class="tab-panel active" id="panel-monitor" role="tabpanel" aria-labelledby="tab-monitor">
        <section>
          <details open>
            <summary>State</summary>
            <div class="state" id="state"></div>
          </details>
        </section>
        <section>
          <details open>
            <summary>Console Log</summary>
            <textarea class="inspect" id="logs" readonly spellcheck="false"></textarea>
          </details>
        </section>
	      </div>
	      <div class="tab-panel" id="panel-config" role="tabpanel" aria-labelledby="tab-config">
        <section>
          <details open>
            <summary>Connectivity</summary>
            <div class="config-controls">
              <div class="field full-row">
                <label>Profile <span class="inline-badge hidden" id="connectivity-auto-badge">Auto</span></label>
                <select id="env-profile"></select>
                <div class="segmented" id="connectivity-mode" role="radiogroup" aria-label="Connectivity">
                  <label><input type="radio" name="connectivity-mode" value="online">Online</label>
                  <label><input type="radio" name="connectivity-mode" value="offline">Offline</label>
                </div>
              </div>
            </div>
          </details>
        </section>
        <section>
          <details id="mcp-servers-details">
            <summary>MCP Servers</summary>
            <div class="mcp-server-panel">
              <div class="mcp-server-toolbar">
                <div class="detail">HTTP MCP admin pages can load through LiveStageAssistant or directly from this browser.</div>
                <div class="segmented" id="mcp-admin-route" role="radiogroup" aria-label="MCP admin route">
                  <label><input type="radio" name="mcp-admin-route" value="proxy">HTTP proxy</label>
                  <label><input type="radio" name="mcp-admin-route" value="direct">Direct</label>
                </div>
              </div>
              <div class="mcp-server-grid" id="mcp-server-grid"></div>
            </div>
          </details>
        </section>
	        <section>
	          <details open>
	            <summary>STT/TTS</summary>
	            <div class="config-controls">
              <div class="field">
                <label for="wake-word">Wake Word</label>
                <input id="wake-word" type="text" placeholder="Disabled">
              </div>
              <div class="field full-row">
                <label for="stt-prompt">STT_PROMPT</label>
                <textarea class="inspect" id="stt-prompt" spellcheck="false"></textarea>
              </div>
              <div class="field">
                <label>Interrompre une conversation</label>
                <div class="segmented" id="interrupt-conversation" role="radiogroup" aria-label="Interrompre une conversation">
                  <label><input type="radio" name="interrupt-conversation" value="off">Off</label>
                  <label><input type="radio" name="interrupt-conversation" value="on">On</label>
                </div>
              </div>
	              <div class="field cloud-audio-control">
	                <label for="cloud-tts-provider">TTS</label>
	                <select id="cloud-tts-provider"></select>
	              </div>
	              <div class="field cloud-audio-control">
	                <label>TTS Output</label>
	                <div class="segmented" id="tts-output" role="radiogroup" aria-label="TTS Output">
	                  <label><input type="radio" name="tts-output" value="browser">Browser</label>
	                  <label><input type="radio" name="tts-output" value="backend">Backend</label>
	                  <label><input type="radio" name="tts-output" value="silent">Silent</label>
	                </div>
	              </div>
	              <div class="offline-audio-summary hidden" id="offline-audio-summary">TTS: local pyttsx3</div>
              <div class="field" id="elevenlabs-voice-field">
                <label for="elevenlabs-voice">ElevenLabs Voice</label>
                <select id="elevenlabs-voice"></select>
              </div>
              <div class="field" id="openai-tts-voice-field">
                <label for="openai-tts-voice">OpenAI Voice</label>
                <select id="openai-tts-voice"></select>
              </div>
              <div class="field" id="tts-speed-field">
                <label for="openai-tts-speed">TTS Speed <span id="openai-tts-speed-label">1.0x</span></label>
                <input id="openai-tts-speed" type="range" min="0.6" max="1.8" step="0.05" value="1">
              </div>
            </div>
          </details>
        </section>
        <section>
          <details id="cloud-api-details">
            <summary>Cloud API</summary>
            <div class="cloud-api-panel">
              <div class="cloud-api-header">
                <div class="detail">API keys stay on the backend. OpenAI credit remaining is not exposed by the public API.</div>
                <button class="small-button" id="cloud-api-refresh" type="button">Refresh</button>
              </div>
              <div class="cloud-api-grid" id="cloud-api-grid"></div>
            </div>
          </details>
        </section>
        <section>
          <details>
            <summary>Audio In/Out</summary>
            <div class="config-controls">
              <div class="field">
                <label for="browser-audio-input">Browser Audio Input</label>
                <select id="browser-audio-input"></select>
              </div>
              <div class="field" id="browser-audio-output-field">
                <label for="browser-audio-output">Browser Audio Output</label>
                <select id="browser-audio-output"></select>
              </div>
              <div class="field">
                <label>&nbsp;</label>
                <button class="small-button" id="browser-audio-refresh" type="button">Refresh</button>
              </div>
              <div class="field">
                <label for="backend-audio-input">Backend Audio Input</label>
                <select id="backend-audio-input"></select>
              </div>
              <div class="field" id="backend-audio-output-field">
                <label for="backend-audio-output">Backend Audio Output</label>
                <select id="backend-audio-output"></select>
              </div>
            </div>
          </details>
        </section>
        <section>
          <details open>
            <summary>IA model</summary>
            <div class="config-controls">
              <div class="field">
                <label for="llm-provider">Provider</label>
                <select id="llm-provider"></select>
              </div>
              <div class="field">
                <label for="llm-model">LLM</label>
                <select id="llm-model"></select>
              </div>
              <div class="field">
                <label for="session-context-size">Session Context <span id="session-context-size-label">6000</span></label>
                <input id="session-context-size" type="range" min="0" max="12000" step="500" value="6000">
              </div>
              <div class="field">
                <label>Tool Routing</label>
                <div class="segmented" id="mcp-tool-routing" role="radiogroup" aria-label="Tool Routing">
                  <label><input type="radio" name="mcp-tool-routing" value="false">Off</label>
                  <label><input type="radio" name="mcp-tool-routing" value="true">Routing</label>
                </div>
              </div>
            </div>
          </details>
        </section>
        <section>
          <details>
            <summary>Other</summary>
            <div class="config-controls">
              <div class="field">
                <label for="thinking-sound">Thinking Sound</label>
                <select id="thinking-sound"></select>
              </div>
            </div>
          </details>
        </section>
        <section>
          <details>
            <summary>Prompt</summary>
            <div class="prompt-fields">
              <div class="field">
                <label for="assistant-system-prompt">ASSISTANT_SYSTEM_PROMPT</label>
                <textarea class="inspect" id="assistant-system-prompt" spellcheck="false"></textarea>
              </div>
              <div class="field">
                <label for="prompt">Final Prompt</label>
                <textarea class="inspect" id="prompt" readonly spellcheck="false"></textarea>
              </div>
            </div>
          </details>
        </section>
        <section>
          <details>
            <summary>Env file</summary>
            <textarea class="inspect" id="config" readonly spellcheck="false"></textarea>
          </details>
        </section>
        <div class="config-footer">
          <div class="config-message" id="llm-message"></div>
          <button id="llm-save" type="button">Save</button>
        </div>
      </div>
    </div>
  </div>

  <div class="loading-overlay" id="session-loading" aria-hidden="true">
    <div class="loading-panel" role="status" aria-live="polite">
      <div class="loading-spinner" aria-hidden="true"></div>
      <div>
        <div class="loading-title" id="session-loading-title">Loading session</div>
        <div class="loading-detail">Preparing persisted context summary</div>
      </div>
    </div>
  </div>

  <div class="session-summary-popover" id="session-summary-popover" role="status" aria-live="polite"></div>

  <script>
    const stateEl = document.querySelector("#state");
    const configEl = document.querySelector("#config");
    const logsEl = document.querySelector("#logs");
    const sttPromptEl = document.querySelector("#stt-prompt");
    const assistantSystemPromptEl = document.querySelector("#assistant-system-prompt");
    const promptEl = document.querySelector("#prompt");
    const metaEl = document.querySelector("#meta");
    const messagesEl = document.querySelector("#messages");
    const chatPanel = document.querySelector("#chat-panel");
    const vncUrl = document.querySelector("#vnc-url");
    const vncConnect = document.querySelector("#vnc-connect");
    const vncFrame = document.querySelector("#vnc-frame");
    const vncStatus = document.querySelector("#vnc-status");
    const sessionList = document.querySelector("#session-list");
    const sessionNew = document.querySelector("#session-new");
    const injectForm = document.querySelector("#inject-form");
    const commandField = document.querySelector("#command-field");
    const soundwave = document.querySelector("#soundwave");
    const injectCommand = document.querySelector("#inject-command");
    const injectStop = document.querySelector("#inject-stop");
    const webConversation = document.querySelector("#web-conversation");
    const webMic = document.querySelector("#web-mic");
    const settingsOpen = document.querySelector("#settings-open");
    const settingsClose = document.querySelector("#settings-close");
    const settingsOverlay = document.querySelector("#settings-overlay");
    const sessionLoading = document.querySelector("#session-loading");
    const sessionLoadingTitle = document.querySelector("#session-loading-title");
    const sessionSummaryPopover = document.querySelector("#session-summary-popover");
    const tabs = Array.from(document.querySelectorAll(".tab"));
    const panels = Array.from(document.querySelectorAll(".tab-panel"));
	    const llmProvider = document.querySelector("#llm-provider");
	    const llmModel = document.querySelector("#llm-model");
	    const sessionContextSize = document.querySelector("#session-context-size");
    const sessionContextSizeLabel = document.querySelector("#session-context-size-label");
    const mcpToolRoutingInputs = Array.from(document.querySelectorAll('input[name="mcp-tool-routing"]'));
    const interruptConversationInputs = Array.from(document.querySelectorAll('input[name="interrupt-conversation"]'));
    const envProfile = document.querySelector("#env-profile");
    const connectivityAutoBadge = document.querySelector("#connectivity-auto-badge");
    const connectivityModeInputs = Array.from(document.querySelectorAll('input[name="connectivity-mode"]'));
    const cloudAudioControls = Array.from(document.querySelectorAll(".cloud-audio-control"));
    const offlineAudioSummary = document.querySelector("#offline-audio-summary");
	    const wakeWord = document.querySelector("#wake-word");
    const cloudTtsProvider = document.querySelector("#cloud-tts-provider");
    const ttsOutputInputs = Array.from(document.querySelectorAll('input[name="tts-output"]'));
    const elevenlabsVoiceField = document.querySelector("#elevenlabs-voice-field");
    const elevenlabsVoice = document.querySelector("#elevenlabs-voice");
    const openaiTtsVoiceField = document.querySelector("#openai-tts-voice-field");
    const openaiTtsVoice = document.querySelector("#openai-tts-voice");
    const ttsSpeedField = document.querySelector("#tts-speed-field");
    const openaiTtsSpeed = document.querySelector("#openai-tts-speed");
    const openaiTtsSpeedLabel = document.querySelector("#openai-tts-speed-label");
    const cloudApiDetails = document.querySelector("#cloud-api-details");
    const cloudApiRefresh = document.querySelector("#cloud-api-refresh");
    const cloudApiGrid = document.querySelector("#cloud-api-grid");
    const mcpServerGrid = document.querySelector("#mcp-server-grid");
    const mcpAdminRouteInputs = Array.from(document.querySelectorAll('input[name="mcp-admin-route"]'));
    const browserAudioInput = document.querySelector("#browser-audio-input");
    const browserAudioOutputField = document.querySelector("#browser-audio-output-field");
    const browserAudioOutput = document.querySelector("#browser-audio-output");
    const browserAudioRefresh = document.querySelector("#browser-audio-refresh");
    const backendAudioInput = document.querySelector("#backend-audio-input");
    const backendAudioOutputField = document.querySelector("#backend-audio-output-field");
    const backendAudioOutput = document.querySelector("#backend-audio-output");
    const thinkingSound = document.querySelector("#thinking-sound");
    const llmSave = document.querySelector("#llm-save");
    const llmMessage = document.querySelector("#llm-message");
    let llmControlsInitialized = false;
    let llmOptionsLoading = false;
    let envProfilesLoading = false;
    let activeEnvProfile = "";
    let envProfileSwitchingEnabled = false;
    let connectivityLocked = false;
    let configBaseline = "";
    let environmentLoadingActive = false;
    let vncConnectTimer = null;
    let vncUrlDirty = false;
    let currentVncFrameUrl = "";
    let currentSnapshotEnvFile = "";
    let metaErrorUntil = 0;
    let lastServerMessages = [];
    let pendingMessages = [];
    let composerLocked = false;
    let cancelRequestInFlight = false;
    let interruptConversationEnabled = false;
    let openSessionMenuId = "";
    let openSessionSummaryId = "";
    let sessionSummaryPinned = false;
    let sessionSummaryHoverTimer = null;
    let sessionSummaryAnchor = null;
    let sessionSummaryHoverId = "";
    let sessionSummaryCache = new Map();
    let lastPointer = { x: -1, y: -1 };
    let webAudio = { enabled: false, stt_enabled: false, tts_enabled: false };
    let mediaRecorder = null;
    let mediaStream = null;
    let recordedChunks = [];
    let isRecording = false;
    let recordingTimer = null;
    let recordingAudioContext = null;
    let recordingAnalyser = null;
    let recordingMonitorId = null;
    let recordingSpeechDetected = false;
    let recordingSilenceStartedAt = null;
    let recordingStartedAt = 0;
    let recordingSpeechCandidateStartedAt = null;
    let recordingSpeechFrames = 0;
    let soundwaveAnimationId = null;
    let soundwaveStartedAt = 0;
    let conversationEnabled = false;
    let conversationRecorder = null;
    let conversationStream = null;
    let conversationAudioContext = null;
    let conversationAnalyser = null;
    let conversationChunks = [];
    let conversationMonitorId = null;
    let conversationSpeechDetected = false;
    let conversationSilenceStartedAt = null;
    let conversationStartedAt = 0;
    let conversationSpeechCandidateStartedAt = null;
    let conversationSpeechFrames = 0;
    let conversationRestartTimer = null;
    let conversationDiscard = false;
    let conversationStopStreamAfterSegment = false;
    let lastSpokenAssistantMessageId = null;
    let webTtsPlaying = false;
    let webTtsAudioContext = null;
    let webTtsUnlocked = false;
    let selectedBrowserAudioInput = window.localStorage.getItem("browser-audio-input") || "";
    let selectedBrowserAudioOutput = window.localStorage.getItem("browser-audio-output") || "";
    let cloudApiLoaded = false;
    let cloudApiLoading = false;
    let mcpServersSignature = "";
    let lastMcpServers = [];
    let currentWebTtsSource = null;
    let currentWebTtsAudio = null;
    let thinkingAudio = null;
    let thinkingAudioUrl = "";
    let thinkingAudioPlaying = false;

    function setMeta(text, mode = "normal", holdMs = 0) {
      metaEl.textContent = text;
      metaEl.classList.toggle("error", mode === "error");
      metaErrorUntil = mode === "error" && holdMs > 0 ? Date.now() + holdMs : 0;
    }

    function conciseClientTtsError(error) {
      const raw = String(error && error.message ? error.message : error || "").trim();
      const lowered = raw.toLowerCase();
      try {
        const parsed = JSON.parse(raw);
        const payload = parsed.error || parsed;
        if (payload && payload.message) return String(payload.message);
      } catch (_) {}
      if (
        lowered.includes("quota_exceeded") ||
        lowered.includes("insufficient_quota") ||
        lowered.includes("exceeds your quota") ||
        lowered.includes("0 credits remaining")
      ) {
        return "Plus de crédit TTS disponible.";
      }
      if (
        lowered.includes("invalid_api_key") ||
        lowered.includes("invalid api key") ||
        lowered.includes("unauthorized") ||
        lowered.includes("status_code: 401") ||
        lowered.includes("status code: 401")
      ) {
        return "Clé API TTS invalide ou refusée.";
      }
      if (
        lowered.includes("rate_limit") ||
        lowered.includes("rate limit") ||
        lowered.includes("too many requests") ||
        lowered.includes("status_code: 429") ||
        lowered.includes("status code: 429")
      ) {
        return "Limite TTS atteinte, réessaie dans un moment.";
      }
      if (lowered.includes("credit") || lowered.includes("billing") || lowered.includes("payment")) {
        return "Problème de crédit ou facturation TTS.";
      }
      const text = raw.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
      return text ? `Erreur TTS web: ${text.slice(0, 140)}` : "Erreur TTS web.";
    }

    function formatNumber(value) {
      if (value === null || value === undefined || value === "") return "n/a";
      const number = Number(value);
      if (!Number.isFinite(number)) return String(value);
      return new Intl.NumberFormat(undefined).format(number);
    }

    function formatCurrency(value, currency = "usd") {
      const number = Number(value || 0);
      if (!Number.isFinite(number)) return "n/a";
      try {
        return new Intl.NumberFormat(undefined, { style: "currency", currency: String(currency || "usd").toUpperCase() }).format(number);
      } catch (_) {
        return `${number.toFixed(2)} ${String(currency || "usd").toUpperCase()}`;
      }
    }

    function cloudApiCard(provider, item) {
      const status = item && item.status ? item.status : "unknown";
      const statusClass = status === "ok" ? "ok" : status === "missing" || status === "unavailable" ? "warn" : "bad";
      const lines = Array.isArray(item && item.lines) ? item.lines : [];
      const maskedKey = item && item.masked_key ? item.masked_key : "non configurée";
      return `
        <div class="cloud-api-card">
          <div class="cloud-api-title">
            <span>${escapeHtml(provider)}</span>
            <span class="cloud-api-status ${statusClass}">${escapeHtml(status)}</span>
          </div>
          <div class="cloud-api-key">Key: ${escapeHtml(maskedKey)}</div>
          ${lines.map((line) => `<div class="cloud-api-line">${escapeHtml(line)}</div>`).join("")}
        </div>
      `;
    }

    function renderCloudApiStatus(data) {
      const openai = data.openai || {};
      const elevenlabs = data.elevenlabs || {};
      const openaiLines = Array.isArray(openai.lines) ? openai.lines.slice() : [];
      if (openai.cost_7d) {
        openaiLines.unshift(`Coût 7 jours: ${formatCurrency(openai.cost_7d.value, openai.cost_7d.currency)}`);
      }
      const elevenLines = Array.isArray(elevenlabs.lines) ? elevenlabs.lines.slice() : [];
      if (elevenlabs.characters) {
        elevenLines.unshift(
          `Caractères restants: ${formatNumber(elevenlabs.characters.remaining)} / ${formatNumber(elevenlabs.characters.limit)}`
        );
        elevenLines.unshift(`Caractères utilisés: ${formatNumber(elevenlabs.characters.used)}`);
      }
      cloudApiGrid.innerHTML = [
        cloudApiCard("OpenAI", { ...openai, lines: openaiLines }),
        cloudApiCard("ElevenLabs", { ...elevenlabs, lines: elevenLines })
      ].join("");
    }

    async function loadCloudApiStatus(force = false) {
      if (cloudApiLoading || (!force && cloudApiLoaded)) return;
      cloudApiLoading = true;
      cloudApiRefresh.disabled = true;
      cloudApiGrid.innerHTML = '<div class="cloud-api-line">Chargement...</div>';
      try {
        const response = await fetch("/api/cloud-api-status", { cache: "no-store" });
        const text = await response.text();
        if (!response.ok) throw new Error(text);
        renderCloudApiStatus(JSON.parse(text));
        cloudApiLoaded = true;
      } catch (error) {
        cloudApiGrid.innerHTML = `<div class="cloud-api-line">${escapeHtml(`Cloud API unavailable: ${error}`)}</div>`;
      } finally {
        cloudApiLoading = false;
        cloudApiRefresh.disabled = false;
      }
    }

    function escapeHtml(value) {
      return String(value || "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }

    function selectedMcpAdminRoute() {
      const checked = mcpAdminRouteInputs.find((input) => input.checked);
      return checked && checked.value === "direct" ? "direct" : "proxy";
    }

    function setSelectedMcpAdminRoute(value) {
      const normalized = value === "direct" ? "direct" : "proxy";
      for (const input of mcpAdminRouteInputs) {
        input.checked = input.value === normalized;
      }
    }

    function mcpServerAdminUrl(server, route) {
      if (route === "direct") return server.admin_url || "";
      return server.proxy_admin_url || server.admin_url || "";
    }

    function mcpServerRouteDetail(route) {
      if (route === "direct") {
        return "Direct mode loads the MCP server from this browser. Use it only when this device can reach that address.";
      }
      return "HTTP proxy mode loads the MCP admin page through LiveStageAssistant, so only the backend needs access.";
    }

    function renderMcpServers(servers) {
      const items = Array.isArray(servers) ? servers : [];
      lastMcpServers = items;
      const route = selectedMcpAdminRoute();
      const signature = JSON.stringify(items.map((item) => [
        item.name || "",
        item.type || "",
        item.admin_url || "",
        item.proxy_admin_url || "",
        Boolean(item.embeddable),
        Boolean(item.auth_required),
        item.detail || ""
      ])) + "|" + route;
      if (signature === mcpServersSignature) return;
      mcpServersSignature = signature;
      mcpServerGrid.replaceChildren();

      if (!items.length) {
        const empty = document.createElement("div");
        empty.className = "mcp-server-empty";
        empty.textContent = "No MCP servers loaded from the active config.";
        mcpServerGrid.append(empty);
        return;
      }

      for (const server of items) {
        const card = document.createElement("div");
        card.className = "mcp-server-card";

        const head = document.createElement("div");
        head.className = "mcp-server-head";

        const title = document.createElement("div");
        title.className = "mcp-server-title";
        const name = document.createElement("div");
        name.className = "mcp-server-name";
        name.textContent = server.name || "MCP server";
        const url = document.createElement("div");
        url.className = "mcp-server-url";
        url.textContent = server.admin_url || server.detail || "No browser admin URL";
        title.append(name, url);

        const actions = document.createElement("div");
        actions.className = "mcp-server-actions";
        const badge = document.createElement("span");
        badge.className = "inline-badge";
        badge.textContent = server.auth_required ? "Auth" : (server.type || "MCP");
        actions.append(badge);
        const selectedUrl = mcpServerAdminUrl(server, route);
        if (selectedUrl) {
          const open = document.createElement("a");
          open.className = "mcp-server-open";
          open.href = selectedUrl;
          open.target = "_blank";
          open.rel = "noreferrer";
          open.textContent = route === "direct" ? "Open direct" : "Open via proxy";
          actions.append(open);
        }
        const alternateUrl = route === "direct" ? server.proxy_admin_url : server.admin_url;
        if (alternateUrl && alternateUrl !== selectedUrl) {
          const alternate = document.createElement("a");
          alternate.className = "mcp-server-open";
          alternate.href = alternateUrl;
          alternate.target = "_blank";
          alternate.rel = "noreferrer";
          alternate.textContent = route === "direct" ? "Proxy" : "Direct";
          actions.append(alternate);
        }

        head.append(title, actions);
        card.append(head);

        if (server.embeddable && selectedUrl) {
          const placeholder = document.createElement("div");
          placeholder.className = "mcp-server-placeholder";

          const note = document.createElement("div");
          note.className = "detail";
          note.textContent = mcpServerRouteDetail(route);

          const load = document.createElement("button");
          load.className = "mcp-server-load";
          load.type = "button";
          load.textContent = "Load frame";
          load.addEventListener("click", () => {
            const frame = document.createElement("iframe");
            frame.className = "mcp-server-frame";
            frame.title = `${server.name || "MCP server"} admin`;
            frame.referrerPolicy = "no-referrer";
            frame.src = selectedUrl;
            placeholder.replaceWith(frame);
          });

          placeholder.append(note, load);
          card.append(placeholder);
        } else {
          const empty = document.createElement("div");
          empty.className = "mcp-server-empty";
          empty.textContent = server.detail || "This MCP server does not expose a browser page.";
          card.append(empty);
        }

        mcpServerGrid.append(card);
      }
    }

    function isStopCommand(value) {
      const normalized = String(value || "")
        .toLowerCase()
        .normalize("NFD")
        .replace(/[\\u0300-\\u036f]/g, "")
        .replace(/[^\\w'-]+/g, " ")
        .trim();
      if (!normalized) return false;
      const stopWords = new Set(["stop", "stoppe", "stope", "arrete", "arreter", "annule", "annuler", "cancel"]);
      return normalized.split(/\\s+/).some((word) => stopWords.has(word));
    }

    function setVncStatus(value) {
      const status = value || "hors ligne";
      vncStatus.textContent = status;
      vncStatus.classList.toggle("online", status.startsWith("connecté") || status.startsWith("Connecté"));
      vncStatus.classList.toggle("offline", status.startsWith("hors ligne"));
    }

    function noVncUrlFromInput(value) {
      const raw = String(value || "").trim();
      if (!raw) return "";
      const lowerRaw = raw.toLowerCase();
      if (lowerRaw.startsWith("http://") || lowerRaw.startsWith("https://")) return raw;

      const parsed = new URL(lowerRaw.startsWith("vnc:") ? `http:${raw.slice(4)}` : raw);
      const params = new URLSearchParams();
      params.set("host", parsed.hostname);
      params.set("port", parsed.port || "5900");
      params.set("autoconnect", "1");
      params.set("resize", "scale");
      const password = parsed.searchParams.get("password") || "ronron";
      if (password) params.set("password", password);
      return `/vnc.html?${params.toString()}`;
    }

    async function saveRemoteScreenUrl() {
      const nextUrl = vncUrl.value.trim();
      const response = await fetch("/api/remote-screen-config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ vnc_url: nextUrl })
      });
      if (!response.ok) throw new Error(await response.text());
      vncUrlDirty = false;
      return response.json();
    }

    function disconnectVnc(status = "hors ligne") {
      window.clearTimeout(vncConnectTimer);
      currentVncFrameUrl = "";
      vncFrame.src = "about:blank";
      setVncStatus(status);
    }

    async function connectVnc({ save = false, force = false } = {}) {
      let frameUrl = "";
      try {
        if (save) await saveRemoteScreenUrl();
        frameUrl = noVncUrlFromInput(vncUrl.value);
      } catch (error) {
        setVncStatus("hors ligne");
        metaEl.textContent = `VNC URL error: ${error}`;
        return;
      }
      if (!frameUrl) {
        setVncStatus("hors ligne");
        return;
      }
      if (!force && frameUrl === currentVncFrameUrl && vncFrame.src) {
        return;
      }
      setVncStatus("connexion...");
      window.clearTimeout(vncConnectTimer);
      vncConnectTimer = window.setTimeout(() => setVncStatus("hors ligne"), 5000);
      try {
        const targetUrl = new URL(frameUrl, window.location.href);
        if (targetUrl.origin === window.location.origin) {
          const response = await fetch(targetUrl.href, { method: "GET", cache: "no-store" });
          if (!response.ok) {
            window.clearTimeout(vncConnectTimer);
            setVncStatus("hors ligne");
            metaEl.textContent = `noVNC indisponible: ${targetUrl.pathname}`;
            return;
          }
        }
      } catch (error) {
        window.clearTimeout(vncConnectTimer);
        setVncStatus("hors ligne");
        metaEl.textContent = `noVNC indisponible: ${error}`;
        return;
      }
      if (currentVncFrameUrl && (force || currentVncFrameUrl !== frameUrl)) {
        vncFrame.src = "about:blank";
        await new Promise((resolve) => window.setTimeout(resolve, 0));
      }
      currentVncFrameUrl = frameUrl;
      vncFrame.src = frameUrl;
    }

    function ledClass(status) {
      const value = String(status || "unknown").toLowerCase();
      if (["online", "initialized", "ready", "ok", "configured"].includes(value)) return "ok";
      if (["initializing", "reload", "unknown", "warning"].includes(value)) return "warn";
      if (["offline", "error", "failed"].includes(value)) return "bad";
      return "idle";
    }

    function tile(title, status, detail) {
      return `<div class="tile">
        <div class="tile-title"><span class="led ${ledClass(status)}"></span><span>${escapeHtml(title)}</span></div>
        <div>${escapeHtml(status || "unknown")}</div>
        <div class="detail">${escapeHtml(detail || "")}</div>
      </div>`;
    }

    function messageBubble(message) {
      const role = message.role === "user" ? "user" : "assistant";
      const pending = message.pending ? " pending" : "";
      const contextIncluded = message.context_included ? " context-included" : "";
      return `<div class="message-row ${role}${pending}${contextIncluded}">
        <div class="bubble">${escapeHtml(message.text)}</div>
      </div>`;
    }

    function canHoverSessionSummary() {
      return window.matchMedia("(hover: hover) and (pointer: fine)").matches;
    }

    function sessionSummaryForId(sessionId) {
      return String(sessionSummaryCache.get(sessionId) || "").trim();
    }

    function clearSessionSummaryHoverTimer() {
      if (sessionSummaryHoverTimer) {
        window.clearTimeout(sessionSummaryHoverTimer);
        sessionSummaryHoverTimer = null;
      }
    }

    function setSessionSummaryButtonsExpanded(sessionId, expanded) {
      for (const button of sessionList.querySelectorAll(".session-summary-button")) {
        const row = button.closest(".session-row");
        button.setAttribute("aria-expanded", expanded && row?.dataset.sessionId === sessionId ? "true" : "false");
      }
    }

    function placeSessionSummaryPopover(anchorRect) {
      if (!anchorRect) return;
      const compact = window.matchMedia("(max-width: 720px), (hover: none), (pointer: coarse)").matches;
      const gap = 8;
      const margin = 12;
      const rect = sessionSummaryPopover.getBoundingClientRect();
      let left = compact ? anchorRect.left : anchorRect.right + gap;
      let top = compact ? anchorRect.bottom + gap : anchorRect.top;
      left = Math.max(margin, Math.min(left, window.innerWidth - rect.width - margin));
      top = Math.max(margin, Math.min(top, window.innerHeight - rect.height - margin));
      sessionSummaryPopover.style.left = `${left}px`;
      sessionSummaryPopover.style.top = `${top}px`;
    }

    function openSessionSummary(sessionId, anchorElement, { pinned = false } = {}) {
      const summary = sessionSummaryForId(sessionId);
      if (!summary || !anchorElement) return;
      closeSessionMenus();
      clearSessionSummaryHoverTimer();
      if (openSessionSummaryId === sessionId && sessionSummaryPopover.classList.contains("open")) {
        sessionSummaryPinned = sessionSummaryPinned || Boolean(pinned);
        sessionSummaryPopover.textContent = summary;
        setSessionSummaryButtonsExpanded(sessionId, true);
        return;
      }
      openSessionSummaryId = sessionId;
      sessionSummaryPinned = Boolean(pinned);
      sessionSummaryHoverId = "";
      const rect = anchorElement.getBoundingClientRect();
      sessionSummaryAnchor = {
        left: rect.left,
        right: rect.right,
        top: rect.top,
        bottom: rect.bottom
      };
      sessionSummaryPopover.textContent = summary;
      sessionSummaryPopover.classList.add("open");
      placeSessionSummaryPopover(sessionSummaryAnchor);
      setSessionSummaryButtonsExpanded(sessionId, true);
    }

    function closeSessionSummary() {
      clearSessionSummaryHoverTimer();
      openSessionSummaryId = "";
      sessionSummaryPinned = false;
      sessionSummaryAnchor = null;
      sessionSummaryHoverId = "";
      sessionSummaryPopover.classList.remove("open");
      sessionSummaryPopover.textContent = "";
      sessionSummaryPopover.style.left = "";
      sessionSummaryPopover.style.top = "";
      setSessionSummaryButtonsExpanded("", false);
    }

    function pointerIsOnSessionSummaryTarget(sessionId) {
      if (lastPointer.x < 0 || lastPointer.y < 0) return false;
      const target = document.elementFromPoint(lastPointer.x, lastPointer.y);
      if (!target) return false;
      if (target.closest("#session-summary-popover")) return true;
      const row = target.closest(".session-row");
      return row?.dataset.sessionId === sessionId;
    }

    function scheduleSessionSummary(sessionId) {
      if (openSessionSummaryId === sessionId && sessionSummaryPopover.classList.contains("open")) {
        return;
      }
      clearSessionSummaryHoverTimer();
      sessionSummaryHoverId = sessionId;
      sessionSummaryHoverTimer = window.setTimeout(() => {
        if (openSessionSummaryId === sessionId && sessionSummaryPopover.classList.contains("open")) return;
        const row = Array.from(sessionList.querySelectorAll(".session-row"))
          .find((candidate) => candidate.dataset.sessionId === sessionId);
        if (!row || !pointerIsOnSessionSummaryTarget(sessionId)) return;
        openSessionSummary(sessionId, row);
      }, 650);
    }

    function closeSessionSummaryAfterPointerCheck(sessionId) {
      window.setTimeout(() => {
        if (
          (openSessionSummaryId === sessionId || sessionSummaryHoverId === sessionId) &&
          !pointerIsOnSessionSummaryTarget(sessionId)
        ) {
          closeSessionSummary();
        }
      }, 80);
    }

    function syncOpenSessionSummaryAfterRender() {
      if (openSessionSummaryId && !sessionSummaryForId(openSessionSummaryId)) {
        closeSessionSummary();
        return;
      }
      if (openSessionSummaryId) {
        sessionSummaryPopover.textContent = sessionSummaryForId(openSessionSummaryId);
        setSessionSummaryButtonsExpanded(openSessionSummaryId, true);
        if (!sessionSummaryPinned && !pointerIsOnSessionSummaryTarget(openSessionSummaryId)) {
          closeSessionSummary();
        }
      }
    }

    function sessionButton(session, activeId) {
      const active = session.id === activeId ? " active" : "";
      const menuOpen = session.id === openSessionMenuId ? " menu-open" : "";
      const llmSummary = String(session.llm_summary || "").trim();
      const hasSummary = Boolean(llmSummary);
      const summaryClass = hasSummary ? " has-summary" : "";
      const summaryButton = hasSummary
        ? `<button class="session-summary-button" type="button" title="Afficher le llm_summary" aria-label="Afficher le llm_summary" aria-expanded="${session.id === openSessionSummaryId ? "true" : "false"}">i</button>`
        : "";
      const summaryTime = Number(session.llm_summary_updated_at || 0);
      const summaryLabel = summaryTime
        ? new Date(summaryTime * 1000).toLocaleString("fr-FR")
        : "No summary";
      return `<div class="session-row${active}${menuOpen}${summaryClass}" data-session-id="${escapeHtml(session.id)}" data-session-title="${escapeHtml(session.title || "Untitled session")}">
        <button class="session-main" type="button">
          <span class="session-title">${escapeHtml(session.title || "Untitled session")}</span>
          <span class="session-meta">${escapeHtml(summaryLabel)}</span>
        </button>
        <button class="session-menu-button" type="button" title="Session actions" aria-label="Session actions">...</button>
        ${summaryButton}
        <div class="session-menu">
          <button class="session-menu-action" type="button" data-session-action="rename">Rename</button>
          <button class="session-menu-action" type="button" data-session-action="clear">Clear conversation</button>
          <button class="session-menu-action" type="button" data-session-action="save-context">Save context</button>
          <button class="session-menu-action danger" type="button" data-session-action="delete">Delete</button>
        </div>
      </div>`;
    }

    function renderSessions(sessionContext) {
      const context = sessionContext || {};
      const sessions = context.sessions || [];
      const activeId = context.active_id || "";
      sessionSummaryCache = new Map(
        sessions
          .map((session) => [String(session.id || ""), String(session.llm_summary || "").trim()])
          .filter(([sessionId, summary]) => sessionId && summary)
      );
      if (sessions.length === 0) {
        openSessionMenuId = "";
        closeSessionSummary();
        sessionList.innerHTML = `<div class="session-meta">No session</div>`;
        return;
      }
      if (openSessionMenuId && !sessions.some((session) => session.id === openSessionMenuId)) {
        openSessionMenuId = "";
      }
      if (openSessionSummaryId && !sessions.some((session) => session.id === openSessionSummaryId)) {
        closeSessionSummary();
      }
      sessionList.innerHTML = sessions.map((session) => sessionButton(session, activeId)).join("");
      syncOpenSessionSummaryAfterRender();
    }

    function syncSessionContextSizeLabel() {
      const value = Number(sessionContextSize.value || 0);
      sessionContextSizeLabel.textContent = value === 0 ? "Off" : String(value);
    }

    function setSessionContextSize(value) {
      const nextValue = Math.max(0, Math.min(12000, Number(value || 0)));
      sessionContextSize.value = String(nextValue);
      syncSessionContextSizeLabel();
    }

    function summaryLineForMessage(message) {
      const role = message.role === "user" ? "User" : "Assistant";
      const text = String(message.text || "").replace(/\\s+/g, " ").trim();
      return text ? `${role}: ${text}` : "";
    }

    function contextIncludedMessageIds(messages, maxChars) {
      const limit = Math.max(0, Math.min(12000, Number(maxChars || 0)));
      const included = new Set();
      if (limit === 0) return included;

      let candidates = [...(messages || [])];
      if (candidates.length > 0 && candidates[candidates.length - 1].role === "user") {
        candidates = candidates.slice(0, -1);
      }

      let total = 0;
      const selected = [];
      for (let index = candidates.length - 1; index >= 0; index -= 1) {
        const message = candidates[index];
        const line = summaryLineForMessage(message);
        if (!line) continue;
        let lineLength = line.length + 1;
        if (selected.length > 0 && total + lineLength > limit) break;
        selected.push(message.id);
        if (lineLength > limit) lineLength = limit + 1;
        total += lineLength;
      }

      for (const id of selected) included.add(String(id));
      return included;
    }

    function withContextPreview(messages) {
      const includedIds = contextIncludedMessageIds(messages, sessionContextSize.value);
      return (messages || []).map((message) => ({
        ...message,
        context_included: includedIds.has(String(message.id))
      }));
    }

    function thinkingBubble() {
      return `<div class="message-row assistant pending" aria-live="polite" aria-label="Assistant is thinking">
        <div class="bubble">
          <div class="thinking-bubble">
            <span class="thinking-dot"></span>
            <span class="thinking-dot"></span>
            <span class="thinking-dot"></span>
          </div>
        </div>
      </div>`;
    }

    function setComposerLocked(locked) {
      const wasLocked = composerLocked;
      composerLocked = Boolean(locked);
      injectForm.classList.toggle("busy", composerLocked);
      injectStop.classList.toggle("visible", composerLocked);
      injectStop.disabled = !composerLocked || cancelRequestInFlight;
      webConversation.disabled = !webAudio.stt_enabled;
      webMic.disabled = (composerLocked && !interruptConversationEnabled) || !webAudio.stt_enabled || isRecording || conversationEnabled;
      injectCommand.placeholder = composerLocked ? "Assistant is thinking..." : "Message";
      if (wasLocked && !composerLocked && !settingsOverlay.classList.contains("open")) {
        window.setTimeout(() => injectCommand.focus({ preventScroll: true }), 0);
      }
    }

    function setRecording(recording) {
      isRecording = Boolean(recording);
      webMic.classList.toggle("recording", isRecording);
      webMic.innerHTML = isRecording ? "&#9632;" : "🎙️";
      webMic.title = isRecording ? "Stop recording" : "Voice input";
      webMic.setAttribute("aria-label", isRecording ? "Stop recording" : "Voice input");
      webMic.disabled = (composerLocked && !interruptConversationEnabled && !isRecording) || !webAudio.stt_enabled || conversationEnabled;
      injectCommand.placeholder = isRecording ? "Recording..." : (composerLocked ? "Assistant is thinking..." : "Message");
    }

    function updateConversationButton() {
      webConversation.classList.toggle("active", conversationEnabled);
      webConversation.title = conversationEnabled ? "Stop conversation mode" : "Conversation mode";
      webConversation.setAttribute("aria-label", conversationEnabled ? "Stop conversation mode" : "Conversation mode");
      webConversation.disabled = !webAudio.stt_enabled;
      webMic.disabled = (composerLocked && !interruptConversationEnabled && !isRecording) || !webAudio.stt_enabled || conversationEnabled;
    }

    function clearRecordingTimer() {
      if (recordingTimer) {
        window.clearTimeout(recordingTimer);
        recordingTimer = null;
      }
    }

    function blobToBase64(blob) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onloadend = () => {
          const result = String(reader.result || "");
          resolve(result.includes(",") ? result.split(",").pop() : result);
        };
        reader.onerror = reject;
        reader.readAsDataURL(blob);
      });
    }

    function browserAudioConstraints() {
      if (!selectedBrowserAudioInput) return { audio: true };
      return { audio: { deviceId: { exact: selectedBrowserAudioInput } } };
    }

    function supportsBrowserAudioOutputSelection() {
      return typeof HTMLMediaElement !== "undefined" && "setSinkId" in HTMLMediaElement.prototype;
    }

    async function applyBrowserAudioOutput(audio) {
      if (!audio || !selectedBrowserAudioOutput || typeof audio.setSinkId !== "function") return;
      await audio.setSinkId(selectedBrowserAudioOutput);
    }

    async function loadBrowserAudioDevices(requestPermission = false) {
      browserAudioInput.replaceChildren(option("Default browser input", "", false, !selectedBrowserAudioInput));
      browserAudioOutput.replaceChildren(option("Default browser output", "", false, !selectedBrowserAudioOutput));
      if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) {
        browserAudioInput.replaceChildren(option("Browser devices unavailable", "", true, true));
        browserAudioOutput.replaceChildren(option("Browser devices unavailable", "", true, true));
        browserAudioInput.disabled = true;
        browserAudioOutput.disabled = true;
        browserAudioRefresh.disabled = true;
        return;
      }

      let permissionStream = null;
      if (requestPermission && navigator.mediaDevices.getUserMedia) {
        try {
          permissionStream = await navigator.mediaDevices.getUserMedia(browserAudioConstraints());
        } catch (error) {
          metaEl.textContent = `browser audio devices unavailable: ${error}`;
        } finally {
          if (permissionStream) {
            for (const track of permissionStream.getTracks()) track.stop();
          }
        }
      }

      try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        const inputs = devices.filter((device) => device.kind === "audioinput");
        const outputs = devices.filter((device) => device.kind === "audiooutput");

        browserAudioInput.replaceChildren(option("Default browser input", "", false, !selectedBrowserAudioInput));
        inputs.forEach((device, index) => {
          const label = device.label || `Microphone ${index + 1}`;
          browserAudioInput.appendChild(option(label, device.deviceId, false, device.deviceId === selectedBrowserAudioInput));
        });
        if (selectedBrowserAudioInput && ![...browserAudioInput.options].some((item) => item.value === selectedBrowserAudioInput)) {
          browserAudioInput.appendChild(option(`${selectedBrowserAudioInput} (current unavailable)`, selectedBrowserAudioInput, false, true));
        }

        const canSelectOutput = supportsBrowserAudioOutputSelection();
        if (!canSelectOutput) {
          browserAudioOutput.replaceChildren(option("Output selection unsupported", "", true, true));
        } else {
          browserAudioOutput.replaceChildren(option("Default browser output", "", false, !selectedBrowserAudioOutput));
          outputs.forEach((device, index) => {
            const label = device.label || `Speaker ${index + 1}`;
            browserAudioOutput.appendChild(option(label, device.deviceId, false, device.deviceId === selectedBrowserAudioOutput));
          });
          if (selectedBrowserAudioOutput && ![...browserAudioOutput.options].some((item) => item.value === selectedBrowserAudioOutput)) {
            browserAudioOutput.appendChild(option(`${selectedBrowserAudioOutput} (current unavailable)`, selectedBrowserAudioOutput, false, true));
          }
        }

        browserAudioInput.disabled = inputs.length === 0;
        browserAudioOutput.disabled = !canSelectOutput || browserAudioOutput.options.length === 0;
        browserAudioRefresh.disabled = false;
      } catch (error) {
        browserAudioInput.replaceChildren(option("Could not list devices", "", true, true));
        browserAudioOutput.replaceChildren(option("Could not list devices", "", true, true));
        browserAudioInput.disabled = true;
        browserAudioOutput.disabled = true;
        metaEl.textContent = `browser audio devices unavailable: ${error}`;
      }
    }

    function base64ToArrayBuffer(base64) {
      const binary = window.atob(base64);
      const bytes = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index += 1) {
        bytes[index] = binary.charCodeAt(index);
      }
      return bytes.buffer;
    }

    async function unlockWebTtsAudio() {
      if (!webAudio.tts_enabled || webTtsUnlocked) return;
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) return;
      try {
        if (!webTtsAudioContext) webTtsAudioContext = new AudioContextClass();
        if (webTtsAudioContext.state === "suspended") await webTtsAudioContext.resume();
        const buffer = webTtsAudioContext.createBuffer(1, 1, 22050);
        const source = webTtsAudioContext.createBufferSource();
        source.buffer = buffer;
        source.connect(webTtsAudioContext.destination);
        source.start(0);
        webTtsUnlocked = webTtsAudioContext.state === "running";
      } catch (error) {
        webTtsUnlocked = false;
      }
    }

    async function playWebTtsBuffer(audioBase64) {
      if (!webTtsAudioContext || webTtsAudioContext.state !== "running") return false;
      try {
        const arrayBuffer = base64ToArrayBuffer(audioBase64);
        const audioBuffer = await webTtsAudioContext.decodeAudioData(arrayBuffer.slice(0));
        const source = webTtsAudioContext.createBufferSource();
        source.buffer = audioBuffer;
        source.playbackRate.value = 1;
        source.connect(webTtsAudioContext.destination);
        currentWebTtsSource = source;
        await new Promise((resolve, reject) => {
          source.addEventListener("ended", resolve, { once: true });
          try {
            source.start(0);
          } catch (error) {
            reject(error);
          }
        });
        return true;
      } catch (error) {
        currentWebTtsSource = null;
        return false;
      }
    }

    async function playWebTtsElement(audioBase64, mimeType) {
      const arrayBuffer = base64ToArrayBuffer(audioBase64);
      const blob = new Blob([arrayBuffer], { type: mimeType || "audio/mpeg" });
      const objectUrl = URL.createObjectURL(blob);
      const audio = new Audio(objectUrl);
      currentWebTtsAudio = audio;
      audio.playbackRate = Math.max(0.6, Math.min(1.8, Number(webAudio.tts_speed || 1)));
      audio.preservesPitch = true;
      audio.mozPreservesPitch = true;
      audio.webkitPreservesPitch = true;
      await applyBrowserAudioOutput(audio);
      try {
        await new Promise((resolve, reject) => {
          audio.addEventListener("ended", resolve, { once: true });
          audio.addEventListener("error", reject, { once: true });
          audio.play().catch(reject);
        });
      } finally {
        URL.revokeObjectURL(objectUrl);
      }
    }

    function stopMediaStream() {
      stopSoundwave();
      clearRecordingTimer();
      if (recordingMonitorId) {
        window.cancelAnimationFrame(recordingMonitorId);
        recordingMonitorId = null;
      }
      if (recordingAudioContext) {
        recordingAudioContext.close().catch(() => {});
        recordingAudioContext = null;
      }
      if (mediaStream) {
        for (const track of mediaStream.getTracks()) {
          track.stop();
        }
      }
      mediaStream = null;
      recordingAnalyser = null;
    }

    function analyserRms(analyser) {
      if (!analyser) return 0;
      const data = new Uint8Array(analyser.fftSize);
      analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (const value of data) {
        const centered = (value - 128) / 128;
        sum += centered * centered;
      }
      return Math.sqrt(sum / data.length);
    }

    function recordingRms() {
      return analyserRms(recordingAnalyser);
    }

    function activeSoundwaveAnalyser() {
      if (isRecording && recordingAnalyser) return recordingAnalyser;
      if (
        conversationEnabled &&
        conversationRecorder &&
        conversationRecorder.state !== "inactive" &&
        conversationAnalyser
      ) {
        return conversationAnalyser;
      }
      return null;
    }

    function resizeSoundwaveCanvas() {
      const rect = soundwave.getBoundingClientRect();
      const scale = window.devicePixelRatio || 1;
      const width = Math.max(1, Math.floor(rect.width * scale));
      const height = Math.max(1, Math.floor(rect.height * scale));
      if (soundwave.width !== width || soundwave.height !== height) {
        soundwave.width = width;
        soundwave.height = height;
      }
      return { width, height, scale };
    }

    function drawSoundwave() {
      if (!soundwaveAnimationId) return;
      const ctx = soundwave.getContext("2d");
      if (!ctx) return;

      const { width, height, scale } = resizeSoundwaveCanvas();
      const cssWidth = width / scale;
      const cssHeight = height / scale;
      ctx.setTransform(scale, 0, 0, scale, 0, 0);
      ctx.clearRect(0, 0, cssWidth, cssHeight);

      const time = (Date.now() - soundwaveStartedAt) / 1000;
      const centerY = cssHeight / 2;
      const padding = 10;
      const usableWidth = Math.max(1, cssWidth - padding * 2);
      const analyser = activeSoundwaveAnalyser();
      const samples = analyser ? new Uint8Array(analyser.fftSize) : null;
      if (analyser && samples) analyser.getByteTimeDomainData(samples);

      const gradient = ctx.createLinearGradient(padding, 0, cssWidth - padding, 0);
      gradient.addColorStop(0, "rgba(16, 185, 129, 0.25)");
      gradient.addColorStop(0.5, "rgba(59, 130, 246, 0.85)");
      gradient.addColorStop(1, "rgba(16, 185, 129, 0.25)");
      ctx.lineWidth = 2.4;
      ctx.lineCap = "round";
      ctx.strokeStyle = gradient;
      ctx.beginPath();

      const points = 96;
      for (let point = 0; point < points; point += 1) {
        const ratio = point / (points - 1);
        const x = padding + ratio * usableWidth;
        let normalized = Math.sin(ratio * Math.PI * 8 + time * 4.5) * 0.12;
        if (samples) {
          const sampleIndex = Math.min(samples.length - 1, Math.floor(ratio * samples.length));
          normalized = (samples[sampleIndex] - 128) / 128;
        }
        const envelope = Math.sin(ratio * Math.PI);
        const y = centerY + normalized * envelope * cssHeight * 0.42;
        if (point === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();

      soundwaveAnimationId = window.requestAnimationFrame(drawSoundwave);
    }

    function startSoundwave() {
      commandField.classList.add("soundwave-active");
      soundwaveStartedAt = Date.now();
      if (soundwaveAnimationId) window.cancelAnimationFrame(soundwaveAnimationId);
      soundwaveAnimationId = window.requestAnimationFrame(drawSoundwave);
    }

    function stopSoundwave() {
      commandField.classList.remove("soundwave-active");
      if (soundwaveAnimationId) {
        window.cancelAnimationFrame(soundwaveAnimationId);
        soundwaveAnimationId = null;
      }
      const ctx = soundwave.getContext("2d");
      if (ctx) ctx.clearRect(0, 0, soundwave.width, soundwave.height);
    }

    async function requestSilentCancel() {
      stopWebTts();
      if (cancelRequestInFlight) return;
      cancelRequestInFlight = true;
      try {
        const response = await fetch("/api/cancel-command", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({})
        });
        if (!response.ok) throw new Error(await response.text());
      } finally {
        cancelRequestInFlight = false;
      }
    }

    async function submitCommand(command, options = {}) {
      const cleanedCommand = command.trim();
      if (!cleanedCommand) return;
      const shouldInterrupt = Boolean(options.interrupt) && interruptConversationEnabled && (composerLocked || webTtsPlaying);
      if (composerLocked && !shouldInterrupt) return;
      if (shouldInterrupt) {
        await requestSilentCancel();
      }

      pendingMessages.push({
        id: `pending-${Date.now()}`,
        role: "user",
        text: cleanedCommand,
        pending: true,
        sentAt: Date.now()
      });
      setComposerLocked(true);
      renderMessages(lastServerMessages, true);
      try {
        const response = await fetch("/api/inject-command", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ command: cleanedCommand })
        });
        if (!response.ok) throw new Error(await response.text());
        injectCommand.value = "";
        autoSizeComposer();
        await refresh();
      } catch (error) {
        pendingMessages = pendingMessages.filter((message) => message.text !== cleanedCommand);
        setComposerLocked(false);
        renderMessages(lastServerMessages, false);
        metaEl.textContent = `inject failed: ${error}`;
      }
    }

    async function submitComposerCommand() {
      await unlockWebTtsAudio();
      if (composerLocked && interruptConversationEnabled) {
        const command = injectCommand.value.trim();
        if (command) {
          await submitCommand(command, { interrupt: true });
          return;
        }
      }
      if (composerLocked) {
        await cancelCommand();
        return;
      }
      const command = injectCommand.value.trim();
      if (!command) return;
      if (isStopCommand(command)) {
        await cancelCommand(true);
        injectCommand.value = "";
        autoSizeComposer();
        return;
      }
      await submitCommand(command, { interrupt: interruptConversationEnabled });
    }

    async function handleRecordedAudio(blob, options = {}) {
      if (!blob || blob.size === 0) return;
      webMic.disabled = true;
      injectCommand.placeholder = "Transcribing...";
      try {
        const audioBase64 = await blobToBase64(blob);
        const response = await fetch("/api/web-transcribe", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            audio_base64: audioBase64,
            mime_type: blob.type || "audio/webm",
            apply_wake_word: Boolean(options.applyWakeWord)
          })
        });
        if (!response.ok) throw new Error(await response.text());
        const data = await response.json();
        if (data.accepted === false) {
          metaEl.textContent = data.message || "voice ignored";
          if (options.conversation) scheduleConversationRestart();
          return;
        }
        const text = String(data.command_text || data.text || "").trim();
        if (text) {
          if (!options.conversation) injectCommand.value = text;
          autoSizeComposer();
          await submitCommand(text, { interrupt: Boolean(options.conversation) });
        } else if (options.conversation) {
          scheduleConversationRestart();
        }
      } catch (error) {
        metaEl.textContent = `voice input failed: ${error}`;
      } finally {
        setRecording(false);
      }
    }

    async function startWebRecording() {
      if (!webAudio.stt_enabled || isRecording) return;
      if (composerLocked && !interruptConversationEnabled) return;
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
        metaEl.textContent = "voice input unavailable in this browser";
        return;
      }

      try {
        recordedChunks = [];
        recordingSpeechDetected = false;
        recordingSilenceStartedAt = null;
        recordingStartedAt = Date.now();
        recordingSpeechCandidateStartedAt = null;
        recordingSpeechFrames = 0;
        mediaStream = await navigator.mediaDevices.getUserMedia(browserAudioConstraints());
        loadBrowserAudioDevices(false);
        if (AudioContextClass) {
          recordingAudioContext = new AudioContextClass();
          const source = recordingAudioContext.createMediaStreamSource(mediaStream);
          recordingAnalyser = recordingAudioContext.createAnalyser();
          recordingAnalyser.fftSize = 2048;
          source.connect(recordingAnalyser);
        }
        mediaRecorder = new MediaRecorder(mediaStream);
        mediaRecorder.addEventListener("dataavailable", (event) => {
          if (event.data && event.data.size > 0) recordedChunks.push(event.data);
        });
        mediaRecorder.addEventListener("stop", () => {
          const blob = new Blob(recordedChunks, { type: mediaRecorder.mimeType || "audio/webm" });
          const hadRecordingAnalyser = Boolean(recordingAnalyser);
          stopMediaStream();
          if (hadRecordingAnalyser && !recordingSpeechDetected) {
            injectCommand.placeholder = "Message";
            setRecording(false);
            metaEl.textContent = "voice ignored: not enough speech";
            return;
          }
          injectCommand.placeholder = "Transcribing...";
          handleRecordedAudio(blob);
        });
        mediaRecorder.start();
        setRecording(true);
        startSoundwave();
        const maxRecordingMs = Math.max(1000, Number(webAudio.max_record_seconds || 8) * 1000);
        recordingTimer = window.setTimeout(() => stopWebRecording(), maxRecordingMs);
        monitorPushToTalkAudio();
      } catch (error) {
        stopMediaStream();
        setRecording(false);
        metaEl.textContent = `microphone unavailable: ${error}`;
      }
    }

    function stopWebRecording() {
      if (mediaRecorder && mediaRecorder.state !== "inactive") {
        clearRecordingTimer();
        webMic.disabled = true;
        mediaRecorder.stop();
      }
    }

    function monitorPushToTalkAudio() {
      if (!mediaRecorder || mediaRecorder.state === "inactive" || !recordingAnalyser) return;
      const now = Date.now();
      const rms = recordingRms();
      const threshold = Number(webAudio.conversation_threshold || 0.05);
      const silenceMs = Math.max(250, Number(webAudio.conversation_silence_ms || 1200));
      const minSpeechMs = Math.max(0, Number(webAudio.conversation_min_speech_ms || 350));
      const minSpeechFrames = Math.max(0, Number(webAudio.conversation_min_speech_frames || 8));

      if (rms > threshold) {
        if (!recordingSpeechCandidateStartedAt) {
          recordingSpeechCandidateStartedAt = now;
          recordingSpeechFrames = 0;
        }
        recordingSpeechFrames += 1;
        if (
          !recordingSpeechDetected &&
          now - recordingSpeechCandidateStartedAt >= minSpeechMs &&
          recordingSpeechFrames >= minSpeechFrames
        ) {
          recordingSpeechDetected = true;
        }
        recordingSilenceStartedAt = null;
      } else if (recordingSpeechDetected) {
        if (!recordingSilenceStartedAt) recordingSilenceStartedAt = now;
        if (now - recordingSilenceStartedAt >= silenceMs) {
          stopWebRecording();
          return;
        }
      } else {
        recordingSpeechCandidateStartedAt = null;
        recordingSpeechFrames = 0;
      }

      recordingMonitorId = window.requestAnimationFrame(monitorPushToTalkAudio);
    }

    function clearConversationRestartTimer() {
      if (conversationRestartTimer) {
        window.clearTimeout(conversationRestartTimer);
        conversationRestartTimer = null;
      }
    }

    function conversationRms() {
      return analyserRms(conversationAnalyser);
    }

    function stopConversationMonitor() {
      if (conversationMonitorId) {
        window.cancelAnimationFrame(conversationMonitorId);
        conversationMonitorId = null;
      }
    }

    function stopConversationStream() {
      stopConversationMonitor();
      stopSoundwave();
      if (conversationAudioContext) {
        conversationAudioContext.close().catch(() => {});
        conversationAudioContext = null;
      }
      if (conversationStream) {
        for (const track of conversationStream.getTracks()) {
          track.stop();
        }
      }
      conversationStream = null;
      conversationAnalyser = null;
      conversationRecorder = null;
    }

    function stopConversationSegment() {
      stopConversationMonitor();
      stopSoundwave();
      conversationRecorder = null;
    }

    function stopConversationRecording(discard = false, closeStream = false) {
      conversationDiscard = discard;
      conversationStopStreamAfterSegment = Boolean(closeStream);
      if (conversationRecorder && conversationRecorder.state !== "inactive") {
        conversationRecorder.stop();
      } else {
        if (conversationStopStreamAfterSegment) stopConversationStream();
        else stopConversationSegment();
        conversationStopStreamAfterSegment = false;
      }
    }

    async function ensureConversationMicrophone() {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder || !AudioContextClass) {
        throw new Error("conversation mode unavailable in this browser");
      }

      const hasLiveStream = conversationStream
        && conversationStream.getAudioTracks().some((track) => track.readyState === "live");
      if (hasLiveStream && conversationAudioContext && conversationAnalyser) {
        if (conversationAudioContext.state === "suspended") {
          await conversationAudioContext.resume();
        }
        return;
      }

      stopConversationStream();
      conversationStream = await navigator.mediaDevices.getUserMedia(browserAudioConstraints());
      loadBrowserAudioDevices(false);
      conversationAudioContext = new AudioContextClass();
      const source = conversationAudioContext.createMediaStreamSource(conversationStream);
      conversationAnalyser = conversationAudioContext.createAnalyser();
      conversationAnalyser.fftSize = 2048;
      source.connect(conversationAnalyser);
    }

    async function startConversationListening() {
      if (!conversationEnabled || !webAudio.stt_enabled || conversationRecorder) return;
      if ((composerLocked || webTtsPlaying) && !interruptConversationEnabled) return;

      try {
        await ensureConversationMicrophone();
        conversationChunks = [];
        conversationSpeechDetected = false;
        conversationSilenceStartedAt = null;
        conversationSpeechCandidateStartedAt = null;
        conversationSpeechFrames = 0;
        conversationDiscard = false;
        conversationStopStreamAfterSegment = false;
        conversationStartedAt = Date.now();
        conversationRecorder = new MediaRecorder(conversationStream);
        conversationRecorder.addEventListener("dataavailable", (event) => {
          if (event.data && event.data.size > 0) conversationChunks.push(event.data);
        });
        conversationRecorder.addEventListener("stop", () => {
          const shouldDiscard = conversationDiscard || !conversationSpeechDetected;
          const blob = new Blob(conversationChunks, { type: conversationRecorder.mimeType || "audio/webm" });
          const shouldCloseStream = conversationStopStreamAfterSegment;
          if (shouldCloseStream) stopConversationStream();
          else stopConversationSegment();
          conversationStopStreamAfterSegment = false;
          if (shouldCloseStream) return;
          if (!shouldDiscard) {
            handleRecordedAudio(blob, { applyWakeWord: true, conversation: true }).finally(() => {
              scheduleConversationRestart();
            });
          } else {
            scheduleConversationRestart();
          }
        });
        conversationRecorder.start();
        startSoundwave();
        monitorConversationAudio();
        metaEl.textContent = "conversation listening...";
      } catch (error) {
        stopConversationStream();
        metaEl.textContent = `conversation microphone unavailable: ${error}`;
        conversationEnabled = false;
        updateConversationButton();
      }
    }

    function monitorConversationAudio() {
      if (!conversationRecorder || conversationRecorder.state === "inactive") return;
      const now = Date.now();
      const rms = conversationRms();
      const threshold = Number(webAudio.conversation_threshold || 0.05);
      const silenceMs = Math.max(250, Number(webAudio.conversation_silence_ms || 1200));
      const minSpeechMs = Math.max(0, Number(webAudio.conversation_min_speech_ms || 350));
      const minSpeechFrames = Math.max(0, Number(webAudio.conversation_min_speech_frames || 8));
      const maxRecordMs = Math.max(1000, Number(webAudio.max_record_seconds || 8) * 1000);
      const maxIdleMs = Math.max(3000, Number(webAudio.conversation_idle_seconds || 25) * 1000);

      if (rms > threshold) {
        if (!conversationSpeechCandidateStartedAt) {
          conversationSpeechCandidateStartedAt = now;
          conversationSpeechFrames = 0;
        }
        conversationSpeechFrames += 1;
        if (
          !conversationSpeechDetected &&
          now - conversationSpeechCandidateStartedAt >= minSpeechMs &&
          conversationSpeechFrames >= minSpeechFrames
        ) {
          conversationSpeechDetected = true;
        }
        conversationSilenceStartedAt = null;
      } else if (conversationSpeechDetected) {
        if (!conversationSilenceStartedAt) conversationSilenceStartedAt = now;
        if (now - conversationSilenceStartedAt >= silenceMs) {
          stopConversationRecording(false);
          return;
        }
      } else {
        conversationSpeechCandidateStartedAt = null;
        conversationSpeechFrames = 0;
      }

      if (conversationSpeechDetected && now - conversationStartedAt >= maxRecordMs) {
        stopConversationRecording(false);
        return;
      }
      if (!conversationSpeechDetected && now - conversationStartedAt >= maxIdleMs) {
        stopConversationRecording(true);
        return;
      }

      conversationMonitorId = window.requestAnimationFrame(monitorConversationAudio);
    }

    function scheduleConversationRestart(delayMs = 250) {
      clearConversationRestartTimer();
      if (!conversationEnabled) return;
      if ((composerLocked || webTtsPlaying) && !interruptConversationEnabled) return;
      conversationRestartTimer = window.setTimeout(() => startConversationListening(), delayMs);
    }

    function setConversationEnabled(enabled) {
      conversationEnabled = Boolean(enabled);
      updateConversationButton();
      if (conversationEnabled) {
        if (isRecording) stopWebRecording();
        scheduleConversationRestart(0);
      } else {
        clearConversationRestartTimer();
        stopConversationRecording(true, true);
        stopConversationStream();
      }
    }

    async function playWebTts(text) {
      if (!webAudio.tts_enabled || !text) return;
      try {
        webTtsPlaying = true;
        const response = await fetch("/api/web-tts", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text })
        });
        if (!response.ok) {
          const responseText = await response.text();
          throw new Error(responseText);
        }
        const data = await response.json();
        if (!data.audio_base64 || !data.mime_type) return;
        try {
          await playWebTtsElement(data.audio_base64, data.mime_type);
        } catch (audioElementError) {
          const playedWithContext = await playWebTtsBuffer(data.audio_base64);
          if (!playedWithContext) throw audioElementError;
        }
      } catch (error) {
        setMeta(conciseClientTtsError(error), "error", 12000);
      } finally {
        currentWebTtsSource = null;
        currentWebTtsAudio = null;
        webTtsPlaying = false;
        scheduleConversationRestart(250);
      }
    }

    function stopWebTts() {
      if (currentWebTtsSource) {
        try {
          currentWebTtsSource.stop(0);
        } catch (error) {}
        currentWebTtsSource = null;
      }
      if (currentWebTtsAudio) {
        const audio = currentWebTtsAudio;
        currentWebTtsAudio.pause();
        currentWebTtsAudio.currentTime = 0;
        currentWebTtsAudio = null;
        audio.dispatchEvent(new Event("ended"));
      }
      webTtsPlaying = false;
    }

    async function startThinkingAudio() {
      if (!thinkingAudioUrl || thinkingAudioPlaying) return;
      try {
        if (!thinkingAudio || thinkingAudio.src !== new URL(thinkingAudioUrl, window.location.href).href) {
          thinkingAudio = new Audio(thinkingAudioUrl);
          thinkingAudio.loop = true;
          thinkingAudio.volume = 0.75;
        }
        await applyBrowserAudioOutput(thinkingAudio);
        thinkingAudioPlaying = true;
        thinkingAudio.currentTime = 0;
        await thinkingAudio.play();
      } catch (error) {
        thinkingAudioPlaying = false;
      }
    }

    function stopThinkingAudio() {
      if (!thinkingAudio) {
        thinkingAudioPlaying = false;
        return;
      }
      thinkingAudio.pause();
      thinkingAudio.currentTime = 0;
      thinkingAudioPlaying = false;
    }

    async function cancelCommand(force = false) {
      stopWebTts();
      if ((!force && !composerLocked) || cancelRequestInFlight) return;
      cancelRequestInFlight = true;
      injectStop.disabled = true;
      injectCommand.placeholder = "Cancelling...";
      try {
        const response = await fetch("/api/cancel-command", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({})
        });
        if (!response.ok) throw new Error(await response.text());
        await refresh();
      } catch (error) {
        metaEl.textContent = `cancel failed: ${error}`;
      } finally {
        cancelRequestInFlight = false;
        setComposerLocked(composerLocked);
      }
    }

    function renderMessages(serverMessages, showThinking = false) {
      const knownUserMessages = (serverMessages || []).filter((message) => message.role === "user");
      pendingMessages = pendingMessages.filter((pending) => {
        return !knownUserMessages.some((message) => {
          const serverTime = Number(message.created_at || 0) * 1000;
          return message.text === pending.text && serverTime >= pending.sentAt - 1000;
        });
      });

      const rows = [...withContextPreview(serverMessages || []), ...pendingMessages];
      const shouldStick = chatPanel.scrollTop + chatPanel.clientHeight >= chatPanel.scrollHeight - 24;
      if (rows.length === 0) {
        messagesEl.innerHTML = `<div class="empty-state">Live Stage Assistant</div>`;
      } else {
        messagesEl.innerHTML = rows.map(messageBubble).join("") + (showThinking ? thinkingBubble() : "");
      }
      if (shouldStick) {
        chatPanel.scrollTop = chatPanel.scrollHeight;
      }
    }

    function option(label, value, disabled, selected) {
      const opt = document.createElement("option");
      opt.textContent = label;
      opt.value = value;
      opt.disabled = Boolean(disabled);
      opt.selected = Boolean(selected);
      return opt;
    }

    function configSignature() {
      return JSON.stringify({
        env_profile: activeEnvProfile,
        connectivity_mode: selectedConnectivityMode(),
        provider: llmProvider.value || "",
        model: llmModel.value || "",
        session_context_size: Number(sessionContextSize.value || 0),
        mcp_tool_routing_enabled: selectedMcpToolRoutingEnabled(),
        interrupt_conversation_enabled: selectedInterruptConversationEnabled(),
        wake_word: wakeWord.value.trim(),
        stt_prompt: sttPromptEl.value.trim(),
        system_prompt: assistantSystemPromptEl.value.trim(),
        cloud_tts_provider: cloudTtsProvider.value || "",
        tts_output: selectedTtsOutput(),
        backend_audio_input_device: backendAudioInput.value || "",
        backend_audio_output_device: backendAudioOutput.value || "",
        voice_id: elevenlabsVoice.value || "",
        thinking_sound_file: thinkingSound.value || "",
        openai_tts_voice: openaiTtsVoice.value || "",
        openai_tts_speed: Number(openaiTtsSpeed.value || 1)
      });
    }

    function selectedInterruptConversationEnabled() {
      const selected = interruptConversationInputs.find((input) => input.checked);
      return selected ? selected.value === "on" : false;
    }

    function markConfigClean() {
      configBaseline = configSignature();
    }

    function hasUnsavedConfigChanges() {
      return Boolean(configBaseline) && configSignature() !== configBaseline;
    }

    async function loadEnvProfiles() {
      if (envProfilesLoading) return false;
      envProfilesLoading = true;
      let profileChanged = false;
      try {
        const response = await fetch("/api/env-profiles", { cache: "no-store" });
        if (!response.ok) throw new Error(await response.text());
        const data = await response.json();
        const current = data.current || "";
        profileChanged = Boolean(activeEnvProfile) && current && current !== activeEnvProfile;
        activeEnvProfile = current;
        envProfile.replaceChildren();
        for (const profile of data.profiles || []) {
          envProfile.appendChild(option(profile.label || profile.id, profile.id, false, profile.id === current || profile.selected));
        }
        if (current && envProfile.value !== current) {
          envProfile.value = current;
        }
        envProfileSwitchingEnabled = data.switching_enabled !== false;
        connectivityLocked = data.connectivity_locked === true;
        connectivityAutoBadge.classList.toggle("hidden", data.auto_mode !== true);
        envProfile.disabled = !envProfileSwitchingEnabled || envProfile.options.length <= 1;
        if (data.message && !llmMessage.textContent) {
          llmMessage.textContent = data.message;
        }
      } catch (error) {
        envProfile.replaceChildren(option("Env profiles unavailable", "", true, true));
        envProfile.disabled = true;
        connectivityAutoBadge.classList.add("hidden");
        llmMessage.textContent = `Env profiles unavailable: ${error}`;
      } finally {
        envProfilesLoading = false;
      }
      return profileChanged;
    }

    async function switchEnvProfile(nextEnvProfile) {
      if (!nextEnvProfile || nextEnvProfile === activeEnvProfile) {
        envProfile.value = activeEnvProfile;
        return;
      }
      if (hasUnsavedConfigChanges()) {
        const confirmed = window.confirm("Unsaved config changes will be discarded. Switch env profile anyway?");
        if (!confirmed) {
          envProfile.value = activeEnvProfile;
          return;
        }
      }

      envProfile.disabled = true;
      llmSave.disabled = true;
      setEnvironmentLoading(true, "rafraichissement de l'environnement");
      disconnectVnc("reconnexion VNC...");
      llmMessage.textContent = `Switching to ${nextEnvProfile}...`;
      try {
        const response = await fetch("/api/env-profile", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ env_file: nextEnvProfile })
        });
        if (!response.ok) throw new Error(await response.text());
        const data = await response.json();
        activeEnvProfile = data.env_file || nextEnvProfile;
        llmControlsInitialized = false;
        configBaseline = "";
        llmMessage.textContent = data.message || "Env profile switched.";
        await loadEnvProfiles();
        await loadLlmOptions("", "");
        markConfigClean();
        await refresh();
      } catch (error) {
        setEnvironmentLoading(false);
        envProfile.value = activeEnvProfile;
        llmMessage.textContent = `Env switch failed: ${error}`;
        connectVnc({ force: true });
      } finally {
        envProfile.disabled = !envProfileSwitchingEnabled || envProfile.options.length <= 1;
        llmSave.disabled = !llmProvider.value;
      }
    }

    function syncOpenAiSpeedLabel() {
      openaiTtsSpeedLabel.textContent = `${Number(openaiTtsSpeed.value || 1).toFixed(2)}x`;
    }

    function syncAudioDeviceVisibility() {
      const output = selectedTtsOutput();
      browserAudioOutputField.classList.toggle("hidden", output !== "browser");
      backendAudioOutputField.classList.toggle("hidden", output !== "backend");
    }

	    function syncTtsProviderControls() {
      const connectivityMode = selectedConnectivityMode();
      const offline = connectivityMode === "offline";
	      const provider = cloudTtsProvider.value || "none";
	      const output = selectedTtsOutput();
	      const forceSilent = provider === "none";
      for (const element of cloudAudioControls) element.classList.toggle("hidden", offline);
      offlineAudioSummary.classList.toggle("hidden", !offline);
	      for (const input of ttsOutputInputs) {
	        input.disabled = offline || (forceSilent && input.value !== "silent");
	        input.checked = offline ? input.value === "backend" : (forceSilent ? input.value === "silent" : input.value === output);
	      }
	      elevenlabsVoiceField.classList.toggle("hidden", offline || provider !== "elevenlabs");
	      openaiTtsVoiceField.classList.toggle("hidden", offline || provider !== "openai");
	      ttsSpeedField.classList.toggle("hidden", offline || provider === "none");
	      elevenlabsVoice.disabled = offline || provider !== "elevenlabs" || elevenlabsVoice.options.length === 0 || !elevenlabsVoice.value;
	      openaiTtsVoice.disabled = offline || provider !== "openai" || openaiTtsVoice.options.length === 0 || !openaiTtsVoice.value;
	      openaiTtsSpeed.disabled = offline || provider === "none";
      syncAudioDeviceVisibility();
	    }

    function selectedConnectivityMode() {
      const checked = connectivityModeInputs.find((input) => input.checked);
      return checked ? checked.value : "online";
    }

    function setSelectedConnectivityMode(value) {
      const nextValue = value === "offline" ? "offline" : "online";
      for (const input of connectivityModeInputs) {
        input.checked = input.value === nextValue;
      }
    }

    function selectedMcpToolRoutingEnabled() {
      const checked = mcpToolRoutingInputs.find((input) => input.checked);
      return checked ? checked.value === "true" : false;
    }

    function setSelectedMcpToolRoutingEnabled(enabled) {
      const nextValue = enabled ? "true" : "false";
      for (const input of mcpToolRoutingInputs) {
        input.checked = input.value === nextValue;
      }
    }

    function setSelectedInterruptConversationEnabled(enabled) {
      const nextValue = enabled ? "on" : "off";
      for (const input of interruptConversationInputs) {
        input.checked = input.value === nextValue;
      }
    }

    function syncConnectivityLock() {
      for (const input of connectivityModeInputs) {
        input.disabled = connectivityLocked;
      }
    }

    function syncConnectivityControls() {
      if (selectedConnectivityMode() === "offline") {
        if ([...llmProvider.options].some((option) => option.value === "ollama")) {
          llmProvider.value = "ollama";
        }
        if ([...cloudTtsProvider.options].some((option) => option.value === "none")) {
          cloudTtsProvider.value = "none";
        }
        setSelectedTtsOutput("backend");
      }
      syncConnectivityLock();
      syncTtsProviderControls();
    }

    function selectedTtsOutput() {
      const checked = ttsOutputInputs.find((input) => input.checked);
      return checked ? checked.value : "silent";
    }

    function setSelectedTtsOutput(value) {
      const nextValue = value || "silent";
      for (const input of ttsOutputInputs) {
        input.checked = input.value === nextValue;
      }
    }

    function autoSizeComposer() {
      injectCommand.style.height = "0px";
      injectCommand.style.height = `${Math.min(injectCommand.scrollHeight, 160)}px`;
    }

    function setSettingsOpen(open) {
      settingsOverlay.classList.toggle("open", open);
      settingsOverlay.setAttribute("aria-hidden", open ? "false" : "true");
      if (open) settingsClose.focus();
      else settingsOpen.focus();
    }

    function setLoadingOverlay(loading, title = "Loading") {
      sessionLoading.classList.toggle("open", Boolean(loading));
      sessionLoading.setAttribute("aria-hidden", loading ? "false" : "true");
      sessionLoadingTitle.textContent = title;
    }

    function setEnvironmentLoading(loading, title = "rafraichissement de l'environnement") {
      environmentLoadingActive = Boolean(loading);
      setLoadingOverlay(environmentLoadingActive, title);
    }

    function setSessionLoading(loading, title = "Loading session") {
      sessionNew.disabled = Boolean(loading);
      for (const button of sessionList.querySelectorAll(".session-main, .session-menu-button, .session-summary-button, .session-menu-action")) {
        button.disabled = Boolean(loading);
      }
      if (environmentLoadingActive && !loading) return;
      setLoadingOverlay(loading, title);
    }

    function closeSessionMenus() {
      openSessionMenuId = "";
      for (const row of sessionList.querySelectorAll(".session-row.menu-open")) {
        row.classList.remove("menu-open");
      }
    }

    async function renameSession(sessionId, currentTitle) {
      const title = window.prompt("Rename session", currentTitle || "Untitled session");
      if (title === null) return;
      const cleanedTitle = title.trim();
      if (!cleanedTitle) return;
      setSessionLoading(true, "Renaming session");
      try {
        const response = await fetch("/api/session-context/rename", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: sessionId, title: cleanedTitle })
        });
        if (!response.ok) throw new Error(await response.text());
        await refresh();
      } catch (error) {
        metaEl.textContent = `session rename failed: ${error}`;
      } finally {
        setSessionLoading(false);
      }
    }

    async function deleteSession(sessionId, currentTitle) {
      const confirmed = window.confirm(`Delete session "${currentTitle || "Untitled session"}"?`);
      if (!confirmed) return;
      setSessionLoading(true, "Deleting session");
      try {
        const response = await fetch("/api/session-context/delete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: sessionId })
        });
        if (!response.ok) throw new Error(await response.text());
        pendingMessages = [];
        await refresh();
      } catch (error) {
        metaEl.textContent = `session delete failed: ${error}`;
      } finally {
        setSessionLoading(false);
      }
    }

    async function clearSessionConversation(sessionId, currentTitle) {
      const confirmed = window.confirm(
        `Clear visible conversation for "${currentTitle || "Untitled session"}"? The LLM summary will be kept.`
      );
      if (!confirmed) return;
      setSessionLoading(true, "Clearing conversation");
      try {
        const response = await fetch("/api/session-context/clear", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: sessionId })
        });
        if (!response.ok) throw new Error(await response.text());
        pendingMessages = [];
        await refresh();
      } catch (error) {
        metaEl.textContent = `session clear failed: ${error}`;
      } finally {
        setSessionLoading(false);
      }
    }

    async function saveSessionContext(sessionId, currentTitle) {
      setSessionLoading(true, "Saving context");
      try {
        const response = await fetch("/api/session-context/save", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: sessionId })
        });
        if (!response.ok) throw new Error(await response.text());
        await refresh();
        metaEl.textContent = `context saved for ${currentTitle || "Untitled session"}`;
      } catch (error) {
        metaEl.textContent = `context save failed: ${error}`;
      } finally {
        setSessionLoading(false);
      }
    }

    function activateTab(tabId) {
      for (const tab of tabs) {
        const active = tab.id === tabId;
        tab.classList.toggle("active", active);
        tab.setAttribute("aria-selected", active ? "true" : "false");
      }
      for (const panel of panels) {
        panel.classList.toggle("active", panel.getAttribute("aria-labelledby") === tabId);
      }
    }

	    async function loadLlmOptions(provider, preferredModel, connectivityOverride = "") {
      if (llmOptionsLoading) return false;
      const shouldMarkClean = !connectivityOverride;
      llmOptionsLoading = true;
	      llmProvider.disabled = true;
	      llmModel.disabled = true;
      for (const input of connectivityModeInputs) input.disabled = true;
	        sessionContextSize.disabled = true;
      for (const input of mcpToolRoutingInputs) input.disabled = true;
      for (const input of interruptConversationInputs) input.disabled = true;
      wakeWord.disabled = true;
      sttPromptEl.disabled = true;
      assistantSystemPromptEl.disabled = true;
      cloudTtsProvider.disabled = true;
      for (const input of ttsOutputInputs) input.disabled = true;
      elevenlabsVoice.disabled = true;
      openaiTtsVoice.disabled = true;
      openaiTtsSpeed.disabled = true;
      backendAudioInput.disabled = true;
      backendAudioOutput.disabled = true;
      thinkingSound.disabled = true;
      llmSave.disabled = true;
      llmMessage.textContent = "Loading LLM options...";
      try {
        const suffix = provider ? `?provider=${encodeURIComponent(provider)}` : "";
        const response = await fetch(`/api/llm-options${suffix}`, { cache: "no-store" });
        if (!response.ok) throw new Error(await response.text());
        const data = await response.json();

	        const selectedProvider = data.provider || provider || "";
        setSelectedConnectivityMode(connectivityOverride || data.selected_connectivity_mode || "online");
	        llmProvider.replaceChildren();
        for (const item of data.providers || []) {
          const label = item.available === false && item.reason
            ? `${item.label || item.id} (${item.reason})`
            : (item.label || item.id);
          llmProvider.appendChild(option(label, item.id, item.available === false, item.id === selectedProvider));
        }
        if (selectedProvider && llmProvider.value !== selectedProvider) {
          llmProvider.value = selectedProvider;
        }
        setSessionContextSize(data.selected_session_context_size || 0);
        setSelectedMcpToolRoutingEnabled(Boolean(data.selected_mcp_tool_routing_enabled));
        setSelectedInterruptConversationEnabled(Boolean(data.selected_interrupt_conversation_enabled));
        interruptConversationEnabled = selectedInterruptConversationEnabled();
        wakeWord.value = data.selected_wake_word || "";
        sttPromptEl.value = data.selected_stt_prompt || "";
        assistantSystemPromptEl.value = data.selected_system_prompt || "";

        cloudTtsProvider.replaceChildren();
        const selectedCloudTtsProvider = data.selected_cloud_tts_provider || "";
        for (const item of data.cloud_tts_providers || []) {
          cloudTtsProvider.appendChild(option(item.label || item.id, item.id, false, item.id === selectedCloudTtsProvider));
        }
        if (selectedCloudTtsProvider && cloudTtsProvider.value !== selectedCloudTtsProvider) {
          cloudTtsProvider.value = selectedCloudTtsProvider;
        }
        setSelectedTtsOutput(data.selected_tts_output || "silent");

        llmModel.replaceChildren();
        const selectedModel = preferredModel || data.selected_model || "";
        const models = data.models || [];
        if (models.length === 0) {
          llmModel.appendChild(option("No model available", "", true, true));
        } else {
          for (const model of models) {
            llmModel.appendChild(option(model.label || model.id, model.id, false, model.id === selectedModel));
          }
          if (selectedModel && !models.some((model) => model.id === selectedModel)) {
            llmModel.appendChild(option(`${selectedModel} (current)`, selectedModel, false, true));
          }
        }

        elevenlabsVoice.replaceChildren();
        const selectedVoiceId = data.selected_voice_id || "";
        const voices = data.voices || [];
        if (voices.length === 0) {
          elevenlabsVoice.appendChild(option("No voice available", "", true, true));
        } else {
          for (const voice of voices) {
            elevenlabsVoice.appendChild(option(voice.label || voice.id, voice.id, false, voice.id === selectedVoiceId));
          }
          if (selectedVoiceId && !voices.some((voice) => voice.id === selectedVoiceId)) {
            elevenlabsVoice.appendChild(option(`${selectedVoiceId} (current)`, selectedVoiceId, false, true));
          }
        }

        openaiTtsVoice.replaceChildren();
        const selectedOpenAiTtsVoice = data.selected_openai_tts_voice || "";
        const openAiVoices = data.openai_tts_voices || [];
        if (openAiVoices.length === 0) {
          openaiTtsVoice.appendChild(option("No voice available", "", true, true));
        } else {
          for (const voice of openAiVoices) {
            openaiTtsVoice.appendChild(option(voice.label || voice.id, voice.id, false, voice.id === selectedOpenAiTtsVoice));
          }
          if (selectedOpenAiTtsVoice && !openAiVoices.some((voice) => voice.id === selectedOpenAiTtsVoice)) {
            openaiTtsVoice.appendChild(option(`${selectedOpenAiTtsVoice} (current)`, selectedOpenAiTtsVoice, false, true));
          }
        }
	        openaiTtsSpeed.value = String(data.selected_openai_tts_speed || 1.0);
	        syncOpenAiSpeedLabel();
	        syncConnectivityControls();

        backendAudioInput.replaceChildren();
        const selectedBackendAudioInput = data.selected_backend_audio_input_device || "";
        backendAudioInput.appendChild(option("Default input", "", false, !selectedBackendAudioInput));
        for (const device of data.backend_audio_inputs || []) {
          const label = device.default ? `${device.label || device.id} (default)` : (device.label || device.id);
          backendAudioInput.appendChild(option(label, device.id, false, device.id === selectedBackendAudioInput));
        }
        if (selectedBackendAudioInput && ![...backendAudioInput.options].some((item) => item.value === selectedBackendAudioInput)) {
          backendAudioInput.options[0].textContent = `Default input (current unavailable: ${selectedBackendAudioInput})`;
          backendAudioInput.value = "";
        }

        backendAudioOutput.replaceChildren();
        const selectedBackendAudioOutput = data.selected_backend_audio_output_device || "";
        backendAudioOutput.appendChild(option("Default output", "", false, !selectedBackendAudioOutput));
        for (const device of data.backend_audio_outputs || []) {
          const label = device.default ? `${device.label || device.id} (default)` : (device.label || device.id);
          backendAudioOutput.appendChild(option(label, device.id, false, device.id === selectedBackendAudioOutput));
        }
        if (selectedBackendAudioOutput && ![...backendAudioOutput.options].some((item) => item.value === selectedBackendAudioOutput)) {
          backendAudioOutput.options[0].textContent = `Default output (current unavailable: ${selectedBackendAudioOutput})`;
          backendAudioOutput.value = "";
        }

        thinkingSound.replaceChildren();
        const selectedThinkingSound = data.selected_thinking_sound_file || "";
        const sounds = data.thinking_sounds || [];
        if (sounds.length === 0) {
          thinkingSound.appendChild(option("No WAV available", "", true, true));
        } else {
          for (const sound of sounds) {
            thinkingSound.appendChild(option(sound.label || sound.id, sound.id, false, sound.id === selectedThinkingSound));
          }
          if (selectedThinkingSound && !sounds.some((sound) => sound.id === selectedThinkingSound)) {
            thinkingSound.appendChild(option(`${selectedThinkingSound} (current)`, selectedThinkingSound, false, true));
          }
        }

        llmMessage.textContent = data.message || "";
        if (shouldMarkClean) {
          markConfigClean();
        }
      } catch (error) {
        llmMessage.textContent = `LLM options unavailable: ${error}`;
      } finally {
	        llmProvider.disabled = false;
	        llmModel.disabled = llmModel.options.length === 0 || !llmModel.value;
        for (const input of connectivityModeInputs) input.disabled = connectivityLocked;
	        sessionContextSize.disabled = false;
        for (const input of mcpToolRoutingInputs) input.disabled = false;
        for (const input of interruptConversationInputs) input.disabled = false;
        wakeWord.disabled = false;
        sttPromptEl.disabled = false;
        assistantSystemPromptEl.disabled = false;
        cloudTtsProvider.disabled = cloudTtsProvider.options.length === 0 || !cloudTtsProvider.value;
        for (const input of ttsOutputInputs) input.disabled = false;
	        syncConnectivityControls();
        backendAudioInput.disabled = backendAudioInput.options.length === 0;
        backendAudioOutput.disabled = backendAudioOutput.options.length === 0;
        thinkingSound.disabled = thinkingSound.options.length === 0 || !thinkingSound.value;
        llmSave.disabled = !llmProvider.value;
        llmOptionsLoading = false;
      }
      return true;
    }

    async function syncLlmControls(data) {
      if (llmControlsInitialized) return;
      const env = (data.config && data.config.env) || {};
      const provider = String(env.LLM_PROVIDER || "openai").toLowerCase();
      const model = String(env.OPENAI_MODEL || "");
      const loaded = await loadLlmOptions(provider, model);
      if (loaded !== false) {
        llmControlsInitialized = true;
      }
    }

    async function refresh() {
      try {
        const response = await fetch("/api/snapshot", { cache: "no-store" });
        const data = await response.json();
        const previousBusy = composerLocked;
        const snapshotEnvFile = data.env_file || "";
        const snapshotEnvChanged = Boolean(currentSnapshotEnvFile) && snapshotEnvFile && snapshotEnvFile !== currentSnapshotEnvFile;
        if (snapshotEnvFile) {
          currentSnapshotEnvFile = snapshotEnvFile;
        }
        const services = data.services || {};
        const rows = [
          tile("Internet", data.internet, data.mode === "auto" ? "auto profile detection" : "fixed profile"),
          tile("Profile", data.mode, data.env_file || ""),
          ...Object.entries(services).map(([name, service]) => tile(name, service.status, service.detail))
        ];
        stateEl.innerHTML = rows.join("");
        configEl.value = data.config_text || "";
        renderMcpServers(data.mcp_servers || []);
        const remoteScreen = data.remote_screen || {};
        if (!vncUrlDirty && snapshotEnvChanged && currentVncFrameUrl) {
          disconnectVnc("reconnexion VNC...");
        }
        if (!vncUrlDirty && remoteScreen.vnc_url) {
          const remoteScreenUrlChanged = vncUrl.value !== remoteScreen.vnc_url;
          if (remoteScreenUrlChanged) {
            vncUrl.value = remoteScreen.vnc_url;
          }
          let remoteScreenFrameUrl = "";
          try {
            remoteScreenFrameUrl = noVncUrlFromInput(remoteScreen.vnc_url);
          } catch (error) {
            remoteScreenFrameUrl = "";
          }
          if (remoteScreenFrameUrl && (remoteScreenUrlChanged || snapshotEnvChanged || !currentVncFrameUrl)) {
            await connectVnc({ force: true });
          }
        }
        const environmentLoading = data.environment_loading || {};
        setEnvironmentLoading(
          Boolean(environmentLoading.active),
          environmentLoading.title || "rafraichissement de l'environnement"
        );
        const envProfileChanged = await loadEnvProfiles();
        if (envProfileChanged) {
          llmControlsInitialized = false;
          configBaseline = "";
          cloudApiLoaded = false;
        }
        await syncLlmControls(data);
        promptEl.value = data.prompt || "";
        renderSessions(data.session_context || {});
        if (!settingsOverlay.classList.contains("open")) {
          setSessionContextSize(data.session_context_size || 0);
        }
        const shouldStick = logsEl.scrollTop + logsEl.clientHeight >= logsEl.scrollHeight - 8;
        logsEl.value = data.logs || "";
        if (shouldStick) logsEl.scrollTop = logsEl.scrollHeight;
        webAudio = data.web_audio || { enabled: false, stt_enabled: false, tts_enabled: false };
        interruptConversationEnabled = Boolean(webAudio.interrupt_conversation_enabled);
        thinkingAudioUrl = data.thinking_sound_url || "";
        if (!webAudio.stt_enabled && conversationEnabled) {
          setConversationEnabled(false);
        }
        updateConversationButton();
        lastServerMessages = data.messages || [];
        const serverBusy = Boolean(data.assistant_busy);
        const showThinking = serverBusy || pendingMessages.length > 0;
        if (showThinking) startThinkingAudio();
        else stopThinkingAudio();
        setComposerLocked(showThinking);
        renderMessages(lastServerMessages, showThinking);
        const latestAssistantMessage = [...lastServerMessages].reverse().find((message) => message.role === "assistant");
        const latestAssistantMessageAgeMs = latestAssistantMessage && latestAssistantMessage.created_at
          ? Date.now() - Number(latestAssistantMessage.created_at) * 1000
          : Infinity;
        const shouldSpeakLatestAssistant = latestAssistantMessage && (
          previousBusy || (latestAssistantMessage.speak === true && latestAssistantMessageAgeMs < 30000)
        );
        if (
          shouldSpeakLatestAssistant &&
          !showThinking &&
          webAudio.tts_enabled &&
          latestAssistantMessage &&
          latestAssistantMessage.id !== lastSpokenAssistantMessageId
        ) {
          lastSpokenAssistantMessageId = latestAssistantMessage.id;
          playWebTts(latestAssistantMessage.text || "");
        } else if (previousBusy && !showThinking && conversationEnabled) {
          const delay = interruptConversationEnabled
            ? 250
            : webAudio.tts_blocked_by_backend && latestAssistantMessage
            ? Math.min(10000, 1200 + String(latestAssistantMessage.text || "").length * 55)
            : 250;
          scheduleConversationRestart(delay);
        }
        const updated = data.updated_at ? new Date(data.updated_at * 1000).toLocaleTimeString() : "unknown";
        if (Date.now() >= metaErrorUntil) {
          setMeta(`updated ${updated} · uptime ${data.uptime_seconds || 0}s`);
        }
      } catch (error) {
        setMeta(`disconnected: ${error}`, "error", 5000);
      }
    }

    refresh();
    setInterval(refresh, 1500);

    vncUrl.addEventListener("input", () => {
      vncUrlDirty = true;
    });
    vncConnect.addEventListener("click", () => connectVnc({ save: true }));
    vncUrl.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        connectVnc({ save: true });
      }
    });
    vncFrame.addEventListener("load", () => {
      if (currentVncFrameUrl) {
        setVncStatus("connexion...");
      }
    });
    vncFrame.addEventListener("error", () => {
      window.clearTimeout(vncConnectTimer);
      setVncStatus("hors ligne");
    });
    window.addEventListener("message", (event) => {
      if (event.origin !== window.location.origin) return;
      const data = event.data || {};
      if (data.type !== "lsa-vnc-status") return;
      window.clearTimeout(vncConnectTimer);
      setVncStatus(String(data.text || (data.connected ? "connecté" : "hors ligne")));
    });

    injectCommand.addEventListener("input", autoSizeComposer);
    injectCommand.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        submitComposerCommand();
      }
    });

    injectCommand.addEventListener("beforeinput", (event) => {
      if (event.inputType === "insertLineBreak" && !event.shiftKey) {
        event.preventDefault();
        submitComposerCommand();
      }
    });

    injectForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      await submitComposerCommand();
    });

    injectStop.addEventListener("click", async () => {
      await unlockWebTtsAudio();
      await cancelCommand();
    });

    webMic.addEventListener("click", async () => {
      await unlockWebTtsAudio();
      if (isRecording) {
        stopWebRecording();
      } else {
        await startWebRecording();
      }
    });

    webConversation.addEventListener("click", async () => {
      await unlockWebTtsAudio();
      setConversationEnabled(!conversationEnabled);
    });

    document.addEventListener("mousemove", (event) => {
      lastPointer = { x: event.clientX, y: event.clientY };
    });

    sessionNew.addEventListener("click", async () => {
      setSessionLoading(true, "Creating session");
      try {
        const response = await fetch("/api/session-context/new", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({})
        });
        if (!response.ok) throw new Error(await response.text());
        pendingMessages = [];
        await refresh();
      } catch (error) {
        metaEl.textContent = `new session failed: ${error}`;
      } finally {
        setSessionLoading(false);
      }
    });

    sessionList.addEventListener("mouseover", (event) => {
      if (!canHoverSessionSummary()) return;
      lastPointer = { x: event.clientX, y: event.clientY };
      const row = event.target.closest(".session-row.has-summary");
      if (!row || row.contains(event.relatedTarget)) return;
      const sessionId = row.dataset.sessionId || "";
      if (!sessionId) return;
      scheduleSessionSummary(sessionId);
    });

    sessionList.addEventListener("mouseout", (event) => {
      if (!canHoverSessionSummary()) return;
      lastPointer = { x: event.clientX, y: event.clientY };
      const row = event.target.closest(".session-row.has-summary");
      if (!row || row.contains(event.relatedTarget)) return;
      if (openSessionSummaryId === row.dataset.sessionId || sessionSummaryHoverId === row.dataset.sessionId) {
        closeSessionSummaryAfterPointerCheck(row.dataset.sessionId);
      }
    });

    sessionList.addEventListener("click", async (event) => {
      const actionButton = event.target.closest(".session-menu-action");
      const menuButton = event.target.closest(".session-menu-button");
      const summaryButton = event.target.closest(".session-summary-button");
      const mainButton = event.target.closest(".session-main");
      const row = event.target.closest(".session-row");
      if (!row || composerLocked) return;
      const sessionId = row.dataset.sessionId;
      if (!sessionId) return;

      if (actionButton) {
        const action = actionButton.dataset.sessionAction;
        const title = row.dataset.sessionTitle || "Untitled session";
        closeSessionMenus();
        closeSessionSummary();
        if (action === "rename") {
          await renameSession(sessionId, title);
        } else if (action === "clear") {
          await clearSessionConversation(sessionId, title);
        } else if (action === "save-context") {
          await saveSessionContext(sessionId, title);
        } else if (action === "delete") {
          await deleteSession(sessionId, title);
        }
        return;
      }

      if (menuButton) {
        const wasOpen = row.classList.contains("menu-open");
        closeSessionMenus();
        closeSessionSummary();
        if (!wasOpen) {
          openSessionMenuId = sessionId;
          row.classList.add("menu-open");
        }
        return;
      }

      if (summaryButton) {
        const wasOpen = openSessionSummaryId === sessionId && sessionSummaryPinned;
        closeSessionSummary();
        if (!wasOpen) openSessionSummary(sessionId, row, { pinned: true });
        return;
      }

      if (!mainButton) return;
      closeSessionMenus();
      closeSessionSummary();
      setSessionLoading(true, "Loading session");
      try {
        const response = await fetch("/api/session-context/select", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: sessionId })
        });
        if (!response.ok) throw new Error(await response.text());
        pendingMessages = [];
        await refresh();
      } catch (error) {
        metaEl.textContent = `session switch failed: ${error}`;
      } finally {
        setSessionLoading(false);
      }
    });

    document.addEventListener("click", (event) => {
      if (!event.target.closest(".session-row") && !event.target.closest("#session-summary-popover")) {
        closeSessionMenus();
        closeSessionSummary();
      }
    });
    sessionList.addEventListener("scroll", () => closeSessionSummary());
    window.addEventListener("resize", () => {
      if (openSessionSummaryId && sessionSummaryAnchor) {
        placeSessionSummaryPopover(sessionSummaryAnchor);
      }
    });

    settingsOpen.addEventListener("click", () => setSettingsOpen(true));
    settingsClose.addEventListener("click", () => setSettingsOpen(false));
    settingsOverlay.addEventListener("click", (event) => {
      if (event.target === settingsOverlay) setSettingsOpen(false);
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && settingsOverlay.classList.contains("open")) {
        setSettingsOpen(false);
      }
    });
    for (const tab of tabs) {
      tab.addEventListener("click", () => activateTab(tab.id));
    }

	    llmProvider.addEventListener("change", () => {
	      loadLlmOptions(llmProvider.value, "", selectedConnectivityMode());
	    });

    for (const input of connectivityModeInputs) {
      input.addEventListener("change", () => {
        const mode = selectedConnectivityMode();
        loadLlmOptions(mode === "offline" ? "ollama" : "openai", "", mode);
      });
    }

    envProfile.addEventListener("change", () => {
      cloudApiLoaded = false;
      switchEnvProfile(envProfile.value);
    });

	    cloudTtsProvider.addEventListener("change", syncTtsProviderControls);
    for (const input of ttsOutputInputs) {
      input.addEventListener("change", syncTtsProviderControls);
    }
    browserAudioInput.addEventListener("change", () => {
      selectedBrowserAudioInput = browserAudioInput.value;
      window.localStorage.setItem("browser-audio-input", selectedBrowserAudioInput);
      if (conversationEnabled) {
        stopConversationRecording(true, true);
        scheduleConversationRestart(0);
      }
    });
    browserAudioOutput.addEventListener("change", async () => {
      selectedBrowserAudioOutput = browserAudioOutput.value;
      window.localStorage.setItem("browser-audio-output", selectedBrowserAudioOutput);
      try {
        await applyBrowserAudioOutput(thinkingAudio);
        await applyBrowserAudioOutput(currentWebTtsAudio);
      } catch (error) {
        metaEl.textContent = `browser audio output unavailable: ${error}`;
      }
    });
    browserAudioRefresh.addEventListener("click", () => loadBrowserAudioDevices(true));
    sessionContextSize.addEventListener("input", () => {
      syncSessionContextSizeLabel();
      renderMessages(lastServerMessages, composerLocked || pendingMessages.length > 0);
    });
    for (const input of mcpToolRoutingInputs) {
      input.addEventListener("change", () => {});
    }
    setSelectedMcpAdminRoute(window.localStorage.getItem("mcp-admin-route") || "proxy");
    for (const input of mcpAdminRouteInputs) {
      input.addEventListener("change", () => {
        const route = selectedMcpAdminRoute();
        window.localStorage.setItem("mcp-admin-route", route);
        mcpServersSignature = "";
        renderMcpServers(lastMcpServers);
      });
    }
    for (const input of interruptConversationInputs) {
      input.addEventListener("change", () => {
        interruptConversationEnabled = selectedInterruptConversationEnabled();
      });
    }
    cloudApiDetails.addEventListener("toggle", () => {
      if (cloudApiDetails.open) loadCloudApiStatus();
    });
    cloudApiRefresh.addEventListener("click", () => loadCloudApiStatus(true));

    llmSave.addEventListener("click", async () => {
      const provider = llmProvider.value;
      const model = llmModel.value;
      const sessionContextSizeValue = Number(sessionContextSize.value || 0);
      const mcpToolRoutingEnabled = selectedMcpToolRoutingEnabled();
      const interruptConversation = selectedInterruptConversationEnabled();
      const wakeWordValue = wakeWord.value.trim();
	      const sttPromptValue = sttPromptEl.value.trim();
	      const systemPromptValue = assistantSystemPromptEl.value.trim();
      const connectivityModeValue = selectedConnectivityMode();
	      const cloudTtsProviderValue = cloudTtsProvider.value;
      const ttsOutputValue = selectedTtsOutput();
      const backendAudioInputDevice = backendAudioInput.value;
      const backendAudioOutputDevice = backendAudioOutput.value;
      const voiceId = elevenlabsVoice.value;
      const thinkingSoundFile = thinkingSound.value;
      const openAiTtsVoiceValue = openaiTtsVoice.value;
      const openAiTtsSpeedValue = Number(openaiTtsSpeed.value || 1);
      if (!provider) return;

      llmSave.disabled = true;
      llmMessage.textContent = "Saving...";
      try {
        const response = await fetch("/api/llm-config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            provider,
            model,
            session_context_size: sessionContextSizeValue,
            mcp_tool_routing_enabled: mcpToolRoutingEnabled,
            interrupt_conversation_enabled: interruptConversation,
            connectivity_mode: connectivityModeValue,
            cloud_tts_provider: cloudTtsProviderValue,
            tts_output: ttsOutputValue,
            backend_audio_input_device: backendAudioInputDevice,
            backend_audio_output_device: backendAudioOutputDevice,
            wake_word: wakeWordValue,
            stt_prompt: sttPromptValue,
            system_prompt: systemPromptValue,
            voice_id: voiceId,
            thinking_sound_file: thinkingSoundFile,
            openai_tts_voice: openAiTtsVoiceValue,
            openai_tts_speed: openAiTtsSpeedValue
          })
        });
        if (!response.ok) throw new Error(await response.text());
        const data = await response.json();
        llmMessage.textContent = data.message || "Saved.";
        cloudApiLoaded = false;
        setEnvironmentLoading(true, "rafraichissement de l'environnement");
        markConfigClean();
        await refresh();
      } catch (error) {
        setEnvironmentLoading(false);
        llmMessage.textContent = `Save failed: ${error}`;
      } finally {
        llmSave.disabled = !llmProvider.value;
      }
    });

    openaiTtsSpeed.addEventListener("input", syncOpenAiSpeedLabel);
    loadBrowserAudioDevices(false);
  </script>
</body>
</html>
"""
