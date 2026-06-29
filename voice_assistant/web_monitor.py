"""Small read-only web monitor for the voice assistant runtime."""

from __future__ import annotations

import base64
import binascii
from collections import deque
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import mimetypes
import os
from pathlib import Path
import re
import select
import secrets
import socket
import struct
import sys
import threading
import time
from typing import Any, Callable, TextIO
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import parse_qs, quote, unquote, urlparse

try:
    from .i18n import available_locales, load_locale, normalize_locale
    from .speaker_recognition import (
        SPEAKER_EMBEDDING_PREPARATION_MESSAGE,
        SPEAKER_EMBEDDING_READY_MESSAGE,
        compute_missing_resemblyzer_embeddings,
        conventional_speaker_sample_paths,
        resemblyzer_embedding_is_current,
        validate_wav_bytes,
    )
except ImportError:  # pragma: no cover - direct script fallback
    try:
        from i18n import available_locales, load_locale, normalize_locale
        from speaker_recognition import (
            SPEAKER_EMBEDDING_PREPARATION_MESSAGE,
            SPEAKER_EMBEDDING_READY_MESSAGE,
            compute_missing_resemblyzer_embeddings,
            conventional_speaker_sample_paths,
            resemblyzer_embedding_is_current,
            validate_wav_bytes,
        )
    except ImportError:  # pragma: no cover - optional helper unavailable
        available_locales = lambda: [{"id": "fr", "label": "Français"}]
        load_locale = lambda locale=None: {"locale": "fr", "language_name": "Français", "web": {}}
        normalize_locale = lambda locale=None: "fr"
        SPEAKER_EMBEDDING_PREPARATION_MESSAGE = (
            "Je prépare l'empreinte vocale du profil. Cela peut prendre un moment la première fois."
        )
        SPEAKER_EMBEDDING_READY_MESSAGE = "Empreinte vocale calculée."
        compute_missing_resemblyzer_embeddings = None
        conventional_speaker_sample_paths = None
        resemblyzer_embedding_is_current = None
        validate_wav_bytes = None


SECRET_KEY_MARKERS = (
    "API_KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASS",
    "CONNECTION_STRING",
)

LOGGER = logging.getLogger(__name__)
TOOL_RESULT_MARKER = "Tool result:"
COMMAND_ACK_SOUND_CANDIDATES = ("ring.wav", "bell.wav")
WEB_SESSION_COOKIE = "lsa_web_session"


LOGIN_HTML = """<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Live Stage Assistant</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #f6f8fb;
      --surface: #fff;
      --text: #20242b;
      --muted: #68707d;
      --border: #d7dde5;
      --accent: #16833d;
      --bad: #b3261e;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #101418;
        --surface: #171d24;
        --text: #edf2f7;
        --muted: #9aa6b2;
        --border: #303a45;
      }
    }
    * { box-sizing: border-box; }
    body {
      min-height: 100vh;
      margin: 0;
      display: grid;
      place-items: center;
      background: var(--bg);
      color: var(--text);
      font: 16px/1.4 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    form {
      width: min(420px, calc(100vw - 32px));
      display: grid;
      gap: 16px;
      padding: 24px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--surface);
    }
    h1 {
      margin: 0;
      font-size: 24px;
      letter-spacing: 0;
    }
    label {
      display: grid;
      gap: 8px;
      color: var(--muted);
      font-weight: 700;
    }
    input, button {
      width: 100%;
      min-height: 44px;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 0 12px;
      font: inherit;
      color: var(--text);
      background: var(--surface);
    }
    button {
      border-color: var(--accent);
      background: var(--accent);
      color: #fff;
      font-weight: 800;
    }
    .error {
      color: var(--bad);
      font-weight: 700;
    }
  </style>
</head>
<body>
  <form method="post" action="/login">
    <h1>Live Stage Assistant</h1>
    <label>
      Mot de passe
      <input name="password" type="password" autocomplete="current-password" autofocus>
    </label>
    {error}
    <button type="submit">Entrer</button>
  </form>
</body>
</html>
"""


def command_ack_sound_url() -> str:
    """Return the available command acknowledgement asset URL."""
    for filename in COMMAND_ACK_SOUND_CANDIDATES:
        if (Path("assets") / filename).is_file():
            return f"/assets/{filename}"
    return "/assets/ring.wav"


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


def compact_jsonish_spacing(value: str) -> str:
    """Remove JSON formatting whitespace without touching quoted string content."""
    compacted: list[str] = []
    in_string = False
    escape = False

    for char in value:
        if in_string:
            compacted.append(char)
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            compacted.append(char)
            continue
        if char.isspace():
            continue
        compacted.append(char)

    return "".join(compacted)


def compact_tool_result_log_value(value: str) -> str:
    """Compact flattened JSON payloads in mcp-use tool result log lines."""
    if TOOL_RESULT_MARKER not in value:
        return value

    output_lines: list[str] = []
    for line in value.splitlines(keepends=True):
        line_body = line.rstrip("\r\n")
        line_ending = line[len(line_body) :]
        marker_index = line_body.find(TOOL_RESULT_MARKER)
        if marker_index == -1:
            output_lines.append(line)
            continue

        payload_start = marker_index + len(TOOL_RESULT_MARKER)
        prefix = line_body[:payload_start].rstrip()
        payload = line_body[payload_start:].strip()
        if payload.startswith(("{", "[")):
            output_lines.append(f"{prefix} {compact_jsonish_spacing(payload)}{line_ending}")
        else:
            output_lines.append(line)

    return "".join(output_lines)


def mcp_env_value_for_display(value: Any) -> Any:
    """Return a friendlier JSON value for editing MCP server env values."""
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in "{[":
        return value
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


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
            "routing": "",
            "env_options": {},
            "detail": "",
        }
        if not isinstance(server_config, dict):
            entry["detail"] = "invalid MCP server config"
            frames.append(entry)
            continue

        assistant_options = (
            server_config.get("assistantOptions")
            or server_config.get("assistantPrompt")
            or server_config.get("agentPrompt")
        )
        if isinstance(assistant_options, dict):
            routing = assistant_options.get("routing")
            if isinstance(routing, list):
                entry["routing"] = ", ".join(str(item).strip() for item in routing if str(item).strip())
            elif routing is not None:
                entry["routing"] = str(routing)

        server_env = server_config.get("env")
        if isinstance(server_env, dict):
            entry["env_options"] = {
                str(key): mcp_env_value_for_display(value)
                for key, value in sorted(server_env.items())
            }

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

    def __init__(
        self,
        max_log_chars: int = 200_000,
        max_messages: int = 80,
        web_password: str | None = None,
    ):
        self.max_log_chars = max_log_chars
        self.max_messages = max_messages
        self._lock = threading.RLock()
        self._web_password = (web_password or "").strip()
        self._web_sessions: set[str] = set()
        self._log_chunks: deque[str] = deque()
        self._log_chars = 0
        self._messages: deque[dict[str, Any]] = deque()
        self._next_message_id = 1
        self._injected_commands: deque[dict[str, Any]] = deque()
        self._cancel_requested = False
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._stdout_original: TextIO | None = None
        self._stderr_original: TextIO | None = None
        self._logging_handler_streams: list[tuple[logging.StreamHandler, TextIO]] = []
        self._llm_options_handler: Callable[[str | None], dict[str, Any]] | None = None
        self._llm_config_save_handler: Callable[
            [
                str,
                str,
                str,
                str,
                str,
                str,
                str,
                str,
                str,
                int,
                int,
                bool,
                bool,
                str,
                str,
                str,
                str,
                bool,
                str,
                float,
                float,
                float,
                float,
                str,
                float,
                int,
                int,
                int,
                float,
            ],
            dict[str, Any],
        ] | None = None
        self._cloud_api_status_handler: Callable[[], dict[str, Any]] | None = None
        self._env_profile_handler: Callable[[], dict[str, Any]] | None = None
        self._env_profile_switch_handler: Callable[[str], dict[str, Any]] | None = None
        self._remote_screen_save_handler: Callable[[str, bool], dict[str, Any]] | None = None
        self._backend_audio_level_handler: Callable[[str], dict[str, Any]] | None = None
        self._backend_tts_test_handler: Callable[[str, dict[str, Any] | None], dict[str, Any]] | None = None
        self._backend_audio_sample_handler: Callable[[str, dict[str, Any] | None], dict[str, Any]] | None = None
        self._speaker_embedding_notice_handler: Callable[[str], None] | None = None
        self._speaker_profile_update_handler: Callable[[dict[str, Any]], None] | None = None
        self._mcp_routing_save_handler: Callable[[dict[str, str]], dict[str, Any]] | None = None
        self._mcp_server_options_save_handler: Callable[[dict[str, dict[str, Any]]], dict[str, Any]] | None = None
        self._session_context_list_handler: Callable[[], dict[str, Any]] | None = None
        self._session_context_new_handler: Callable[[str | None], dict[str, Any]] | None = None
        self._session_context_select_handler: Callable[[str], dict[str, Any]] | None = None
        self._session_context_rename_handler: Callable[[str, str], dict[str, Any]] | None = None
        self._session_context_clear_handler: Callable[[str], dict[str, Any]] | None = None
        self._session_context_save_handler: Callable[[str], dict[str, Any]] | None = None
        self._session_context_delete_handler: Callable[[str], dict[str, Any]] | None = None
        self._cancel_handler: Callable[[], None] | None = None
        self._web_audio_transcribe_handler: Callable[[bytes, str, bool], dict[str, Any]] | None = None
        self._web_audio_tts_handler: Callable[[str, dict[str, Any] | None], dict[str, Any]] | None = None
        self._mcp_admin_proxy_targets: dict[str, dict[str, Any]] = {}
        self._started_at = time.time()
        self._listen_address: tuple[str, int] | None = None
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
            "remote_screen": {"vnc_url": "vnc://192.168.0.160:5900?password=ronron", "view_only": True},
            "web_audio": {"enabled": False, "stt_enabled": False, "tts_enabled": False},
            "runtime": {},
            "command_ack_sound_url": command_ack_sound_url(),
            "updated_at": time.time(),
        }

    def set_web_password(self, web_password: str | None) -> None:
        password = (web_password or "").strip()
        with self._lock:
            if password != self._web_password:
                self._web_sessions.clear()
            self._web_password = password

    def web_auth_enabled(self) -> bool:
        with self._lock:
            return bool(self._web_password)

    def validate_web_password(self, password: str) -> bool:
        with self._lock:
            expected = self._web_password
        return bool(expected) and hmac.compare_digest(password, expected)

    def create_web_session(self) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._web_sessions.add(token)
        return token

    def has_web_session(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            return token in self._web_sessions

    def render_index_html(self) -> str:
        """Render the monitor shell with the currently selected locale payload."""
        with self._lock:
            env_values = ((self._snapshot.get("config") or {}).get("env") or {}).copy()
        locale = normalize_locale(str(env_values.get("STT_LANGUAGE") or os.getenv("STT_LANGUAGE") or "fr"))
        locale_data = load_locale(locale)
        payload = {
            "locale": locale,
            "messages": locale_data.get("web") or {},
            "available_locales": available_locales(),
        }
        return (
            load_index_html_template()
            .replace("__I18N_LOCALE__", locale)
            .replace("__I18N_PAYLOAD__", json.dumps(payload, ensure_ascii=False))
            .replace(
                "__SPEAKER_EMBEDDING_PREPARATION_MESSAGE__",
                json.dumps(SPEAKER_EMBEDDING_PREPARATION_MESSAGE, ensure_ascii=False),
            )
        )

    def set_llm_config_handlers(
        self,
        *,
        options_handler: Callable[[str | None], dict[str, Any]],
        save_handler: Callable[..., dict[str, Any]],
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

    def set_remote_screen_handler(self, save_handler: Callable[[str, bool], dict[str, Any]]) -> None:
        """Register callback used by the web UI to save remote-screen settings."""
        with self._lock:
            self._remote_screen_save_handler = save_handler

    def set_backend_audio_level_handler(self, handler: Callable[[str], dict[str, Any]]) -> None:
        """Register callback used by the web UI to test backend microphone level."""
        with self._lock:
            self._backend_audio_level_handler = handler

    def set_backend_tts_test_handler(
        self,
        handler: Callable[[str, dict[str, Any] | None], dict[str, Any]],
    ) -> None:
        """Register callback used by the web UI to test backend TTS output."""
        with self._lock:
            self._backend_tts_test_handler = handler

    def set_backend_audio_sample_handler(
        self,
        handler: Callable[[str, dict[str, Any] | None], dict[str, Any]],
    ) -> None:
        """Register callback used by the web UI to preview WAV assets on backend output."""
        with self._lock:
            self._backend_audio_sample_handler = handler

    def set_speaker_embedding_notice_handler(self, handler: Callable[[str], None]) -> None:
        """Register callback used to announce speaker embedding preparation."""
        with self._lock:
            self._speaker_embedding_notice_handler = handler

    def set_speaker_profile_update_handler(self, handler: Callable[[dict[str, Any]], None]) -> None:
        """Register callback invoked after speaker profile samples change."""
        with self._lock:
            self._speaker_profile_update_handler = handler

    def set_mcp_routing_save_handler(self, handler: Callable[[dict[str, str]], dict[str, Any]]) -> None:
        """Register callback used by the web UI to persist MCP routing words."""
        with self._lock:
            self._mcp_routing_save_handler = handler

    def set_mcp_server_options_save_handler(self, handler: Callable[[dict[str, dict[str, Any]]], dict[str, Any]]) -> None:
        """Register callback used by the web UI to persist MCP server env options."""
        with self._lock:
            self._mcp_server_options_save_handler = handler

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
        transcribe_handler: Callable[[bytes, str, bool | str], dict[str, Any]] | None = None,
        tts_handler: Callable[[str, dict[str, Any] | None], dict[str, Any]] | None = None,
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
                    if parsed.path == "/login":
                        self._send_login_page()
                        return
                    if self._auth_required(parsed.path):
                        self._redirect_to_login()
                        return
                    if parsed.path in {"/", "/index.html"}:
                        self._send_text(monitor.render_index_html(), "text/html; charset=utf-8")
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
                    if parsed.path.startswith("/assets/"):
                        self._handle_asset(parsed.path)
                        return
                    self.send_error(404)

                def do_HEAD(self) -> None:
                    parsed = urlparse(self.path)
                    if parsed.path == "/login":
                        self._send_login_page(send_body=False)
                        return
                    if self._auth_required(parsed.path):
                        self._send_auth_required(send_body=False)
                        return
                    if parsed.path in {"/", "/index.html"}:
                        self._send_text(monitor.render_index_html(), "text/html; charset=utf-8", send_body=False)
                        return
                    if parsed.path == "/vnc.html":
                        self._send_text(VNC_HTML, "text/html; charset=utf-8", send_body=False)
                        return
                    if parsed.path.startswith("/api/mcp-admin/"):
                        self._handle_mcp_admin_proxy("HEAD", parsed.path, parsed.query, send_body=False)
                        return
                    if parsed.path.startswith("/assets/"):
                        self._handle_asset(parsed.path, send_body=False)
                        return
                    self.send_error(404)

                def do_POST(self) -> None:
                    parsed = urlparse(self.path)
                    if parsed.path == "/login":
                        self._handle_login()
                        return
                    if self._auth_required(parsed.path):
                        self._send_auth_required()
                        return
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
                    if self.path == "/api/backend-audio-level":
                        self._handle_backend_audio_level()
                        return
                    if self.path == "/api/backend-tts-test":
                        self._handle_backend_tts_test()
                        return
                    if self.path == "/api/backend-audio-sample":
                        self._handle_backend_audio_sample()
                        return
                    if self.path == "/api/speaker-profile-upload":
                        self._handle_speaker_profile_upload()
                        return
                    if self.path == "/api/mcp-routing":
                        self._handle_mcp_routing_save()
                        return
                    if self.path == "/api/mcp-server-options":
                        self._handle_mcp_server_options_save()
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

                def _cookie_value(self, name: str) -> str | None:
                    raw_cookie = self.headers.get("Cookie", "")
                    for chunk in raw_cookie.split(";"):
                        key, _, value = chunk.strip().partition("=")
                        if key == name:
                            return value
                    return None

                def _is_authenticated(self) -> bool:
                    if not monitor.web_auth_enabled():
                        return True
                    return monitor.has_web_session(self._cookie_value(WEB_SESSION_COOKIE))

                def _auth_required(self, _path: str) -> bool:
                    return not self._is_authenticated()

                def _send_login_page(self, *, error: bool = False, send_body: bool = True) -> None:
                    error_html = '<div class="error">Mot de passe incorrect.</div>' if error else ""
                    html = LOGIN_HTML.replace("{error}", error_html)
                    self._send_text(html, "text/html; charset=utf-8", send_body=send_body, no_store=True)

                def _redirect_to_login(self) -> None:
                    self.send_response(303)
                    self.send_header("Location", "/login")
                    self.send_header("Cache-Control", "no-store")
                    self._send_isolation_headers()
                    self.send_header("Content-Length", "0")
                    self.end_headers()

                def _send_auth_required(self, *, send_body: bool = True) -> None:
                    encoded = b"Authentication required" if send_body else b""
                    self.send_response(401)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self._send_isolation_headers()
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    if encoded:
                        self._write_body(encoded)

                def _handle_login(self) -> None:
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                    except ValueError:
                        length = 0
                    if length > 16_384:
                        self.send_error(413, "Login request is too large")
                        return
                    raw_body = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
                    fields = parse_qs(raw_body)
                    password = (fields.get("password") or [""])[0]
                    if not monitor.validate_web_password(password):
                        self._send_login_page(error=True)
                        return
                    token = monitor.create_web_session()
                    self.send_response(303)
                    self.send_header("Location", "/")
                    self.send_header(
                        "Set-Cookie",
                        f"{WEB_SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax",
                    )
                    self.send_header("Cache-Control", "no-store")
                    self._send_isolation_headers()
                    self.send_header("Content-Length", "0")
                    self.end_headers()

                def _send_isolation_headers(self) -> None:
                    self.send_header("Cross-Origin-Opener-Policy", "same-origin")
                    self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
                    self.send_header("Cross-Origin-Resource-Policy", "same-origin")

                def _write_body(self, data: bytes) -> bool:
                    try:
                        self.wfile.write(data)
                        return True
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as e:
                        LOGGER.info("HTTP client disconnected while sending %s: %s", self.path, e)
                        return False
                    except OSError as e:
                        LOGGER.info("HTTP response aborted while sending %s: %s", self.path, e)
                        return False

                def _send_text(
                    self,
                    value: str,
                    content_type: str,
                    *,
                    send_body: bool = True,
                    no_store: bool = False,
                ) -> None:
                    encoded = value.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    if no_store:
                        self.send_header("Cache-Control", "no-store")
                    self._send_isolation_headers()
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    if send_body:
                        self._write_body(encoded)

                def _send_json(self, value: dict[str, Any]) -> None:
                    encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self._send_isolation_headers()
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self._write_body(encoded)

                def _send_json_error(self, status: int, value: dict[str, Any]) -> None:
                    encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self._send_isolation_headers()
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self._write_body(encoded)

                def _handle_asset(self, request_path: str, *, send_body: bool = True) -> None:
                    raw_path = request_path.removeprefix("/assets/")
                    parts = [part for part in raw_path.split("/") if part]
                    if not parts or any(part in {".", ".."} for part in parts):
                        self.send_error(404)
                        return

                    asset_path = Path("assets").joinpath(*parts)
                    try:
                        resolved_root = Path("assets").resolve()
                        resolved_path = asset_path.resolve()
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
                        self.send_error(500, f"Could not read asset: {e}")
                        return

                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header(
                        "Cache-Control",
                        "no-store" if parts[:1] == ["web"] else "public, max-age=3600",
                    )
                    self._send_isolation_headers()
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    if send_body:
                        self._write_body(data)

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
                        self._write_body(data)

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

                def _safe_speaker_profile_slug(self, name: str) -> str:
                    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "_", name.strip().lower()).strip("._-")
                    return normalized[:64] or "speaker"

                def _handle_speaker_profile_upload(self) -> None:
                    payload = self._read_json_body(max_bytes=12 * 1024 * 1024)
                    if payload is None:
                        return

                    profile_name = str(payload.get("profile_name") or "").strip()
                    if not profile_name:
                        self.send_error(400, "Speaker profile name is required")
                        return

                    filename = Path(str(payload.get("filename") or "reference.wav")).name
                    if not filename.lower().endswith(".wav"):
                        self.send_error(400, "Speaker profile upload must be a WAV file")
                        return

                    encoded = str(payload.get("audio_base64") or "")
                    if "," in encoded:
                        encoded = encoded.split(",", 1)[1]
                    try:
                        audio_data = base64.b64decode(encoded, validate=True)
                    except (binascii.Error, ValueError):
                        self.send_error(400, "Invalid speaker profile audio data")
                        return
                    if not audio_data:
                        self.send_error(400, "Speaker profile WAV is empty")
                        return
                    if len(audio_data) > 10 * 1024 * 1024:
                        self.send_error(413, "Speaker profile WAV is too large")
                        return

                    try:
                        if validate_wav_bytes is None:
                            raise ValueError("speaker profile WAV validation is unavailable")
                        validate_wav_bytes(audio_data)
                    except ValueError as e:
                        self.send_error(400, f"Invalid speaker profile WAV: {e}")
                        return

                    try:
                        profile_index = int(payload.get("profile_index") or 0)
                    except (TypeError, ValueError):
                        profile_index = 0
                    if profile_index < 1 or profile_index > 5:
                        self.send_error(400, "Speaker profile index must be between 1 and 5")
                        return
                    try:
                        sample_index = int(payload.get("sample_index") or 0)
                    except (TypeError, ValueError):
                        sample_index = 0
                    if sample_index < 1 or sample_index > 3:
                        self.send_error(400, "Speaker profile sample index must be between 1 and 3")
                        return

                    slug = self._safe_speaker_profile_slug(profile_name)
                    profile_root = Path(os.getenv("SPEAKER_PROFILES_DIR", "data/speaker_profiles"))
                    sample_paths = [profile_root / f"profil{profile_index}_{slot}.wav" for slot in range(1, 4)]
                    try:
                        profile_root.mkdir(parents=True, exist_ok=True)
                        target = sample_paths[sample_index - 1]
                        target.write_bytes(audio_data)
                    except OSError as e:
                        self.send_error(500, f"Could not save speaker profile WAV: {e}")
                        return
                    embedding_status = "embedding pending"
                    embedding_path = target.with_suffix(".npy")
                    embedding_ready = False
                    if (
                        compute_missing_resemblyzer_embeddings is not None
                        and conventional_speaker_sample_paths is not None
                        and resemblyzer_embedding_is_current is not None
                    ):
                        batch_wav_paths = conventional_speaker_sample_paths(profile_root, max_profiles=5, samples_per_profile=3)
                        pending_wav_paths = [
                            wav_path for wav_path in batch_wav_paths
                            if wav_path.exists()
                            and wav_path.is_file()
                            and not resemblyzer_embedding_is_current(wav_path)
                        ]
                        try:
                            with monitor._lock:
                                notice_handler = monitor._speaker_embedding_notice_handler
                            if notice_handler and pending_wav_paths:
                                notice_handler(SPEAKER_EMBEDDING_PREPARATION_MESSAGE)
                            batch_result = compute_missing_resemblyzer_embeddings(batch_wav_paths)
                            computed_count = batch_result.computed_count
                            failed_embeddings = [
                                f"{failed_path.name}: {failed_reason}"
                                for failed_path, failed_reason in batch_result.failed
                            ]
                            embedding_ready = (
                                embedding_path.exists()
                                and embedding_path.is_file()
                                and embedding_path.stat().st_mtime >= target.stat().st_mtime
                            )
                            if embedding_ready:
                                embedding_status = (
                                    f"{computed_count} sample embedding(s) ready"
                                    if computed_count != 1
                                    else "sample embedding ready"
                                )
                            elif failed_embeddings:
                                embedding_status = f"embedding pending: {'; '.join(failed_embeddings)}"
                            else:
                                embedding_status = "embedding pending"
                            if notice_handler and computed_count:
                                notice_handler(SPEAKER_EMBEDDING_READY_MESSAGE)
                        except Exception as e:
                            embedding_status = f"embedding pending: {e}"
                    else:
                        embedding_status = "embedding unavailable"
                    sample_statuses = []
                    for slot_index, sample_path in enumerate(sample_paths, start=1):
                        sample_embedding_path = sample_path.with_suffix(".npy")
                        sample_ready = sample_path.exists() and sample_path.is_file()
                        sample_embedding_ready = (
                            sample_ready
                            and sample_embedding_path.exists()
                            and sample_embedding_path.is_file()
                            and sample_embedding_path.stat().st_mtime >= sample_path.stat().st_mtime
                        )
                        sample_statuses.append(
                            {
                                "index": slot_index,
                                "wav_path": sample_path.as_posix(),
                                "filename": sample_path.name,
                                "ready": sample_ready,
                                "embedding_path": sample_embedding_path.as_posix(),
                                "embedding_ready": sample_embedding_ready,
                            }
                        )
                    samples_complete = all(item["ready"] for item in sample_statuses)
                    samples_available = any(item["ready"] for item in sample_statuses)
                    embedding_count = len([item for item in sample_statuses if item["embedding_ready"]])
                    profile_usable = embedding_count > 0

                    result = {
                        "ok": True,
                        "profile_name": profile_name,
                        "slug": slug,
                        "profile_index": profile_index,
                        "sample_index": sample_index,
                        "wav_path": target.as_posix(),
                        "samples": sample_statuses,
                        "complete": samples_complete,
                        "samples_available": samples_available,
                        "usable": profile_usable,
                        "embedding_count": embedding_count,
                        "embedding_total": len(sample_statuses),
                        "embedding_path": embedding_path.as_posix(),
                        "embedding_ready": bool(embedding_ready),
                        "status": f"{embedding_count}/{len(sample_statuses)} embeddings ready",
                        "embedding_status": embedding_status,
                    }

                    if samples_available:
                        with monitor._lock:
                            update_handler = monitor._speaker_profile_update_handler
                        if update_handler:
                            try:
                                update_handler(result)
                            except Exception as e:
                                LOGGER.warning("Speaker profile update handler failed: %s", e)

                    self._send_json(result)

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

                    payload = self._read_json_body(max_bytes=2 * 1024 * 1024)
                    if payload is None:
                        return

                    provider = str(payload.get("provider") or "").strip().lower()
                    model = str(payload.get("model") or "").strip()
                    if not provider:
                        self.send_error(400, "Provider is required")
                        return
                    cloud_tts_provider = str(payload.get("cloud_tts_provider") or "").strip().lower()
                    tts_output = str(payload.get("tts_output") or "").strip().lower()
                    stt_input = str(payload.get("stt_input") or "both").strip().lower()
                    stt_language = normalize_locale(str(payload.get("stt_language") or "fr"))
                    connectivity_mode = str(payload.get("connectivity_mode") or "").strip().lower()
                    wake_word = str(payload.get("wake_word") or "").strip()
                    stt_prompt = str(payload.get("stt_prompt") or "").strip()
                    system_prompt = str(payload.get("system_prompt") or "").strip()
                    try:
                        session_context_size = int(payload.get("session_context_size") or 0)
                        mcp_agent_max_steps = int(payload.get("mcp_agent_max_steps") or 20)
                    except (TypeError, ValueError):
                        self.send_error(400, "Session context size and MCP max steps must be integers")
                        return
                    mcp_tool_routing_enabled = bool(payload.get("mcp_tool_routing_enabled"))
                    interrupt_conversation_enabled = bool(payload.get("interrupt_conversation_enabled"))
                    backend_audio_input_device = str(payload.get("backend_audio_input_device") or "").strip()
                    backend_audio_output_device = str(payload.get("backend_audio_output_device") or "").strip()
                    backend_audio_monitor_mode = str(payload.get("backend_audio_monitor_mode") or "off").strip().lower()
                    voice_id = str(payload.get("voice_id") or "").strip()
                    thinking_sound_file = str(payload.get("thinking_sound_file") or "").strip()
                    startup_loader_sound_file = str(payload.get("startup_loader_sound_file") or "").strip()
                    command_ack_sound_enabled = bool(payload.get("command_ack_sound_enabled"))
                    openai_tts_voice = str(payload.get("openai_tts_voice") or "").strip()
                    try:
                        openai_tts_speed = float(payload.get("openai_tts_speed") or 1.0)
                    except (TypeError, ValueError):
                        self.send_error(400, "OpenAI TTS speed must be a number")
                        return
                    try:
                        web_tts_volume = float(payload.get("web_tts_volume") if payload.get("web_tts_volume") is not None else 1.0)
                        backend_tts_volume = float(payload.get("backend_tts_volume") if payload.get("backend_tts_volume") is not None else 1.0)
                        backend_audio_output_pan = float(payload.get("backend_audio_output_pan") if payload.get("backend_audio_output_pan") is not None else 0.0)
                        backend_audio_monitor_volume = float(payload.get("backend_audio_monitor_volume") if payload.get("backend_audio_monitor_volume") is not None else 1.0)
                    except (TypeError, ValueError):
                        self.send_error(400, "TTS volume, audio monitor volume, and audio pan must be numbers")
                        return
                    try:
                        vad_speech_threshold = float(payload.get("vad_speech_threshold") or 0.5)
                        vad_negative_threshold = float(payload.get("vad_negative_threshold") or 0.35)
                        vad_min_speech_ms = int(payload.get("vad_min_speech_ms") or 120)
                        vad_min_silence_ms = int(payload.get("vad_min_silence_ms") or 650)
                        vad_speech_pad_ms = int(payload.get("vad_speech_pad_ms") or 100)
                        vad_max_speech_seconds = float(payload.get("vad_max_speech_seconds") or 8.0)
                        speaker_threshold = float(payload.get("speaker_threshold") or 0.75)
                        speaker_margin = float(payload.get("speaker_margin") or 0.10)
                    except (TypeError, ValueError):
                        self.send_error(400, "Voice detection and speaker settings must be numeric")
                        return
                    speaker_recognition_enabled = bool(payload.get("speaker_recognition_enabled"))
                    speaker_backend = str(payload.get("speaker_backend") or "resemblyzer").strip().lower()
                    speaker_profiles = payload.get("speaker_profiles") or []
                    if not isinstance(speaker_profiles, list):
                        self.send_error(400, "speaker_profiles must be a list")
                        return

                    try:
                        result = handler(
                            provider,
                            model,
                            cloud_tts_provider,
                            tts_output,
                            stt_input,
                            stt_language,
                            connectivity_mode,
                            wake_word,
                            stt_prompt,
                            system_prompt,
                            session_context_size,
                            mcp_agent_max_steps,
                            mcp_tool_routing_enabled,
                            interrupt_conversation_enabled,
                            backend_audio_input_device,
                            backend_audio_output_device,
                            voice_id,
                            thinking_sound_file,
                            startup_loader_sound_file,
                            command_ack_sound_enabled,
                            openai_tts_voice,
                            openai_tts_speed,
                            web_tts_volume,
                            backend_tts_volume,
                            backend_audio_output_pan,
                            backend_audio_monitor_mode,
                            backend_audio_monitor_volume,
                            vad_speech_threshold,
                            vad_negative_threshold,
                            vad_min_speech_ms,
                            vad_min_silence_ms,
                            vad_speech_pad_ms,
                            vad_max_speech_seconds,
                            speaker_recognition_enabled,
                            speaker_backend,
                            speaker_threshold,
                            speaker_margin,
                            speaker_profiles,
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
                    view_only = bool(payload.get("view_only"))
                    try:
                        result = handler(vnc_url, view_only)
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
                    speaker = str(payload.get("speaker") or "unknown").strip() or "unknown"
                    try:
                        speaker_confidence = float(payload.get("speaker_confidence") or 0.0)
                    except (TypeError, ValueError):
                        speaker_confidence = 0.0
                    speaker_backend = str(payload.get("speaker_backend") or "none").strip() or "none"
                    try:
                        speaker_second_confidence = float(payload.get("speaker_second_confidence") or 0.0)
                    except (TypeError, ValueError):
                        speaker_second_confidence = 0.0
                    speaker_reason = str(payload.get("speaker_reason") or "").strip()
                    speaker_candidates = payload.get("speaker_candidates") or []
                    if not isinstance(speaker_candidates, list):
                        speaker_candidates = []
                    if not command:
                        self.send_error(400, "Command is required")
                        return

                    monitor.inject_command(
                        command,
                        speaker=speaker,
                        speaker_confidence=speaker_confidence,
                        speaker_backend=speaker_backend,
                        speaker_second_confidence=speaker_second_confidence,
                        speaker_reason=speaker_reason,
                        speaker_candidates=speaker_candidates,
                    )
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

                    wake_word_mode = str(payload.get("wake_word_mode") or "").strip().lower()
                    if wake_word_mode not in {"require", "ignore"}:
                        wake_word_mode = "require" if bool(payload.get("apply_wake_word")) else "ignore"

                    try:
                        result = handler(audio_bytes, mime_type, wake_word_mode)
                    except ValueError as e:
                        self._send_json_error(
                            400,
                            {
                                "ok": False,
                                "error": {
                                    "code": "invalid_audio",
                                    "message": str(e),
                                },
                            },
                        )
                        return
                    except Exception as e:
                        error_text = str(e)
                        status = 429 if "insufficient_quota" in error_text or "Error code: 429" in error_text else 500
                        code = "insufficient_quota" if status == 429 else "transcription_failed"
                        self._send_json_error(
                            status,
                            {
                                "ok": False,
                                "error": {
                                    "code": code,
                                    "message": f"Could not transcribe web audio: {error_text}",
                                },
                            },
                        )
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
                    options = {
                        "provider": str(payload.get("provider") or "").strip().lower(),
                        "model": str(payload.get("model") or "").strip(),
                        "voice": str(payload.get("voice") or "").strip(),
                        "speed": payload.get("speed"),
                        "volume": payload.get("volume"),
                    }

                    try:
                        result = handler(text, options)
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

                def _handle_backend_audio_level(self) -> None:
                    handler = monitor._backend_audio_level_handler
                    if handler is None:
                        self.send_error(503, "Backend audio level test is not available")
                        return

                    payload = self._read_json_body()
                    if payload is None:
                        return
                    device = str(payload.get("device") or "").strip()
                    try:
                        result = handler(device)
                    except ValueError as e:
                        self._send_json_error(400, {"ok": False, "error": {"message": str(e)}})
                        return
                    except Exception as e:
                        self._send_json_error(500, {"ok": False, "error": {"message": f"Could not test backend audio input: {e}"}})
                        return
                    self._send_json(result)

                def _handle_backend_tts_test(self) -> None:
                    handler = monitor._backend_tts_test_handler
                    if handler is None:
                        self.send_error(503, "Backend TTS test is not available")
                        return

                    payload = self._read_json_body()
                    if payload is None:
                        return
                    text = str(payload.get("text") or "").strip()
                    if not text:
                        self.send_error(400, "text is required")
                        return
                    options = {
                        "provider": str(payload.get("provider") or "").strip().lower(),
                        "model": str(payload.get("model") or "").strip(),
                        "voice": str(payload.get("voice") or "").strip(),
                        "speed": payload.get("speed"),
                        "volume": payload.get("volume"),
                        "pan": payload.get("pan"),
                        "output_device": str(payload.get("output_device") or "").strip(),
                    }
                    try:
                        result = handler(text, options)
                    except ValueError as e:
                        self._send_json_error(400, {"ok": False, "error": {"message": str(e)}})
                        return
                    except Exception as e:
                        self._send_json_error(500, {"ok": False, "error": {"message": f"Could not test backend TTS: {e}"}})
                        return
                    self._send_json(result)

                def _handle_backend_audio_sample(self) -> None:
                    handler = monitor._backend_audio_sample_handler
                    if handler is None:
                        self.send_error(503, "Backend audio sample preview is not available")
                        return

                    payload = self._read_json_body()
                    if payload is None:
                        return
                    filename = str(payload.get("filename") or "").strip()
                    if not filename:
                        self.send_error(400, "filename is required")
                        return
                    options = {
                        "volume": payload.get("volume"),
                        "pan": payload.get("pan"),
                        "output_device": str(payload.get("output_device") or "").strip(),
                    }
                    try:
                        result = handler(filename, options)
                    except ValueError as e:
                        self._send_json_error(400, {"ok": False, "error": {"message": str(e)}})
                        return
                    except Exception as e:
                        self._send_json_error(
                            500,
                            {"ok": False, "error": {"message": f"Could not play backend audio sample: {e}"}},
                        )
                        return
                    self._send_json(result)

                def _handle_mcp_routing_save(self) -> None:
                    handler = monitor._mcp_routing_save_handler
                    if handler is None:
                        self.send_error(503, "MCP routing save is not available")
                        return

                    payload = self._read_json_body()
                    if payload is None:
                        return
                    raw_routing = payload.get("routing")
                    if not isinstance(raw_routing, dict):
                        self.send_error(400, "routing must be an object")
                        return
                    routing = {str(name): str(value or "") for name, value in raw_routing.items()}
                    try:
                        result = handler(routing)
                    except ValueError as e:
                        self._send_json_error(400, {"ok": False, "error": {"message": str(e)}})
                        return
                    except Exception as e:
                        self._send_json_error(500, {"ok": False, "error": {"message": f"Could not save MCP routing: {e}"}})
                        return
                    self._send_json(result)

                def _handle_mcp_server_options_save(self) -> None:
                    handler = monitor._mcp_server_options_save_handler
                    if handler is None:
                        self.send_error(503, "MCP server options save is not available")
                        return

                    payload = self._read_json_body(max_bytes=512 * 1024)
                    if payload is None:
                        return
                    raw_options = payload.get("options")
                    if not isinstance(raw_options, dict):
                        self.send_error(400, "options must be an object")
                        return
                    options: dict[str, dict[str, Any]] = {}
                    for name, value in raw_options.items():
                        if not isinstance(value, dict):
                            self.send_error(400, f"options for {name} must be an object")
                            return
                        options[str(name)] = value
                    try:
                        result = handler(options)
                    except ValueError as e:
                        self._send_json_error(400, {"ok": False, "error": {"message": str(e)}})
                        return
                    except Exception as e:
                        self._send_json_error(500, {"ok": False, "error": {"message": f"Could not save MCP server options: {e}"}})
                        return
                    self._send_json(result)

            self._server = ThreadingHTTPServer((host, port), MonitorHandler)
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                name="voice-assistant-web-monitor",
                daemon=True,
            )
            self._thread.start()
            self._listen_address = self._server.server_address
            return self._listen_address

    def stop(self) -> None:
        with self._lock:
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None
            self._listen_address = None

        if server:
            server.shutdown()
            server.server_close()
        if thread:
            thread.join(timeout=2)

    @property
    def listen_address(self) -> tuple[str, int] | None:
        with self._lock:
            return self._listen_address

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

    def inject_command(
        self,
        command: str,
        *,
        speaker: str = "unknown",
        speaker_confidence: float = 0.0,
        speaker_backend: str = "none",
        speaker_second_confidence: float = 0.0,
        speaker_reason: str = "",
        speaker_candidates: list[dict[str, Any]] | None = None,
    ) -> None:
        cleaned_command = command.strip()
        if not cleaned_command:
            return

        with self._lock:
            self._injected_commands.append(
                {
                    "text": cleaned_command,
                    "speaker": speaker or "unknown",
                    "speaker_confidence": float(speaker_confidence or 0.0),
                    "speaker_backend": speaker_backend or "none",
                    "speaker_second_confidence": float(speaker_second_confidence or 0.0),
                    "speaker_reason": speaker_reason or "",
                    "speaker_candidates": speaker_candidates or [],
                }
            )
            self._snapshot["updated_at"] = time.time()

    def pop_injected_command(self) -> dict[str, Any] | None:
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
        return compact_tool_result_log_value(value)

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
        runtime: dict[str, Any] | None = None,
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
            if runtime is not None:
                merged_runtime = dict(self._snapshot.get("runtime") or {})
                merged_runtime.update(runtime)
                self._snapshot["runtime"] = merged_runtime
            if thinking_sound_file is not None:
                cleaned_file = Path(thinking_sound_file).name if thinking_sound_file else ""
                if cleaned_file:
                    self._snapshot["thinking_sound_url"] = f"/assets/{cleaned_file}"
                else:
                    self._snapshot["thinking_sound_url"] = ""
                self._snapshot["command_ack_sound_url"] = command_ack_sound_url()
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

      const { default: RFB } = await import("/assets/web/static/novnc/core/rfb.js?v=lsa-novnc-20260602-1");
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
      const viewOnly = params.get("viewOnly") !== "0";
      rfb.viewOnly = viewOnly;
      rfb.scaleViewport = true;
      rfb.resizeSession = false;
      console.info("[LSA noVNC] RFB instance created");
      rfb.addEventListener("connect", () => {
        console.info("[LSA noVNC] connected");
        setStatus(`Connecté à ${host}:${port}${viewOnly ? " - lecture seule" : ""}`, true);
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


WEB_ASSET_DIR = Path("assets/web")
INDEX_HTML_PATH = WEB_ASSET_DIR / "index.html"


def load_index_html_template() -> str:
    try:
        return INDEX_HTML_PATH.read_text(encoding="utf-8")
    except OSError:
        return """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Live Stage Assistant</title></head>
<body>Web UI assets are missing.</body>
</html>
"""
