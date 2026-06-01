"""Small read-only web monitor for the voice assistant runtime."""

from __future__ import annotations

import base64
import binascii
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import mimetypes
from pathlib import Path
import sys
import threading
import time
from typing import Any, Callable, TextIO
from urllib.parse import parse_qs, urlparse


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


def redact_mapping(values: dict[str, Any]) -> dict[str, Any]:
    return {key: redact_config_value(key, value) for key, value in values.items()}


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
        self._llm_config_save_handler: Callable[[str, str, str, str, str, str, int, str, str, str, float], dict[str, Any]] | None = None
        self._session_context_list_handler: Callable[[], dict[str, Any]] | None = None
        self._session_context_new_handler: Callable[[str | None], dict[str, Any]] | None = None
        self._session_context_select_handler: Callable[[str], dict[str, Any]] | None = None
        self._web_audio_transcribe_handler: Callable[[bytes, str, bool], dict[str, Any]] | None = None
        self._web_audio_tts_handler: Callable[[str], dict[str, Any]] | None = None
        self._started_at = time.time()
        self._snapshot: dict[str, Any] = {
            "mode": "unknown",
            "env_file": None,
            "internet": "unknown",
            "services": {},
            "config": {},
            "config_text": "{}",
            "prompt": "",
            "session_context": {"active_id": "", "sessions": [], "current": {}, "messages": []},
            "session_context_size": 6000,
            "assistant_busy": False,
            "web_audio": {"enabled": False, "stt_enabled": False, "tts_enabled": False},
            "updated_at": time.time(),
        }

    def set_llm_config_handlers(
        self,
        *,
        options_handler: Callable[[str | None], dict[str, Any]],
        save_handler: Callable[[str, str, str, str, str, str, int, str, str, str, float], dict[str, Any]],
    ) -> None:
        """Register callbacks used by the web UI to list and save LLM settings."""
        with self._lock:
            self._llm_options_handler = options_handler
            self._llm_config_save_handler = save_handler

    def set_session_context_handlers(
        self,
        *,
        list_handler: Callable[[], dict[str, Any]],
        new_handler: Callable[[str | None], dict[str, Any]],
        select_handler: Callable[[str], dict[str, Any]],
    ) -> None:
        """Register callbacks used by the web UI to list/create/select chat sessions."""
        with self._lock:
            self._session_context_list_handler = list_handler
            self._session_context_new_handler = new_handler
            self._session_context_select_handler = select_handler

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
                    if parsed.path == "/api/snapshot":
                        self._send_json(monitor.snapshot())
                        return
                    if parsed.path == "/api/llm-options":
                        self._handle_llm_options(parsed.query)
                        return
                    if parsed.path == "/api/session-context":
                        self._handle_session_context_list()
                        return
                    if parsed.path.startswith("/assets/"):
                        self._handle_asset(parsed.path)
                        return
                    self.send_error(404)

                def do_POST(self) -> None:
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
                    if self.path == "/api/session-context/new":
                        self._handle_session_context_new()
                        return
                    if self.path == "/api/session-context/select":
                        self._handle_session_context_select()
                        return
                    self.send_error(404)

                def log_message(self, format: str, *args: Any) -> None:
                    return

                def _send_text(self, value: str, content_type: str) -> None:
                    encoded = value.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)

                def _send_json(self, value: dict[str, Any]) -> None:
                    encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)

                def _handle_asset(self, request_path: str) -> None:
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
                    self.wfile.write(data)

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
                    wake_word = str(payload.get("wake_word") or "").strip()
                    system_prompt = str(payload.get("system_prompt") or "").strip()
                    try:
                        session_context_size = int(payload.get("session_context_size") or 0)
                    except (TypeError, ValueError):
                        self.send_error(400, "Session context size must be an integer")
                        return
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
                            wake_word,
                            system_prompt,
                            session_context_size,
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
                        self.send_error(400, str(e))
                        return
                    except Exception as e:
                        self.send_error(500, f"Could not generate web TTS: {e}")
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
        with self._lock:
            self._cancel_requested = True
            self._snapshot["updated_at"] = time.time()

    def pop_cancel_requested(self) -> bool:
        with self._lock:
            if not self._cancel_requested:
                return False
            self._cancel_requested = False
            self._snapshot["updated_at"] = time.time()
            return True

    def append_dialogue(self, role: str, text: str) -> None:
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
                self._snapshot["config"] = config
                self._snapshot["config_text"] = json.dumps(config, ensure_ascii=False, indent=2)
            if prompt is not None:
                self._snapshot["prompt"] = prompt
            if web_audio is not None:
                self._snapshot["web_audio"] = web_audio
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
    .session-item {
      width: 100%;
      min-height: 42px;
      border: 1px solid transparent;
      border-radius: 8px;
      padding: 8px 9px;
      background: transparent;
      color: var(--text);
      text-align: left;
      display: grid;
      gap: 2px;
    }
    .session-item.active {
      border-color: var(--border);
      background: var(--surface);
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
      min-height: 0;
      overflow-y: auto;
      padding: 22px 16px 16px;
      scroll-behavior: smooth;
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
      grid-template-columns: 40px 40px minmax(0, 1fr) 40px;
      gap: 8px;
      align-items: end;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 8px;
      background: var(--surface);
      box-shadow: 0 8px 32px rgba(0, 0, 0, 0.08);
    }
    #inject-command {
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
    #inject-command:disabled {
      color: var(--muted);
      cursor: not-allowed;
    }
    #inject-submit {
      width: 40px;
      height: 40px;
      min-height: 40px;
      border: 0;
      border-radius: 8px;
      background: var(--text);
      color: var(--bg);
      font-size: 18px;
      line-height: 1;
    }
    #web-mic,
    #web-conversation {
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
    #web-conversation:disabled {
      color: var(--muted);
      cursor: not-allowed;
      opacity: 0.55;
    }
    #inject-submit:disabled {
      background: var(--surface-soft);
      color: var(--muted);
      border: 1px solid var(--border);
    }
    #inject-submit.stop-mode {
      background: var(--surface-soft);
      color: #000000;
      border: 1px solid var(--border);
      font-size: 13px;
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
    .field.hidden {
      display: none;
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
      .bubble { max-width: 88%; }
      .overlay { padding: 8px; }
      .settings-panel { height: 100%; }
      .config-controls { grid-template-columns: 1fr; }
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
      <div class="chat-scroll" id="chat-scroll">
        <div class="messages" id="messages"></div>
      </div>
    </main>

    <div class="composer-wrap">
      <form class="inject-form" id="inject-form">
        <button id="web-conversation" type="button" title="Conversation mode" aria-label="Conversation mode" disabled>&#8734;</button>
        <button id="web-mic" type="button" title="Voice input" aria-label="Voice input" disabled>&#9679;</button>
        <textarea id="inject-command" rows="1" autocomplete="off" placeholder="Message"></textarea>
        <button id="inject-submit" type="submit" title="Send" aria-label="Send">&#8593;</button>
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
            <summary>STT/TTS</summary>
            <div class="config-controls">
              <div class="field">
                <label for="wake-word">Wake Word</label>
                <input id="wake-word" type="text" placeholder="Disabled">
              </div>
              <div class="field">
                <label for="cloud-tts-provider">TTS</label>
                <select id="cloud-tts-provider"></select>
              </div>
              <div class="field">
                <label>TTS Output</label>
                <div class="segmented" id="tts-output" role="radiogroup" aria-label="TTS Output">
                  <label><input type="radio" name="tts-output" value="browser">Browser</label>
                  <label><input type="radio" name="tts-output" value="backend">Backend</label>
                  <label><input type="radio" name="tts-output" value="silent">Silent</label>
                </div>
              </div>
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

  <script>
    const stateEl = document.querySelector("#state");
    const configEl = document.querySelector("#config");
    const logsEl = document.querySelector("#logs");
    const assistantSystemPromptEl = document.querySelector("#assistant-system-prompt");
    const promptEl = document.querySelector("#prompt");
    const metaEl = document.querySelector("#meta");
    const messagesEl = document.querySelector("#messages");
    const chatScroll = document.querySelector("#chat-scroll");
    const sessionList = document.querySelector("#session-list");
    const sessionNew = document.querySelector("#session-new");
    const injectForm = document.querySelector("#inject-form");
    const injectCommand = document.querySelector("#inject-command");
    const injectSubmit = document.querySelector("#inject-submit");
    const webConversation = document.querySelector("#web-conversation");
    const webMic = document.querySelector("#web-mic");
    const settingsOpen = document.querySelector("#settings-open");
    const settingsClose = document.querySelector("#settings-close");
    const settingsOverlay = document.querySelector("#settings-overlay");
    const tabs = Array.from(document.querySelectorAll(".tab"));
    const panels = Array.from(document.querySelectorAll(".tab-panel"));
    const llmProvider = document.querySelector("#llm-provider");
    const llmModel = document.querySelector("#llm-model");
    const sessionContextSize = document.querySelector("#session-context-size");
    const sessionContextSizeLabel = document.querySelector("#session-context-size-label");
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
    const thinkingSound = document.querySelector("#thinking-sound");
    const llmSave = document.querySelector("#llm-save");
    const llmMessage = document.querySelector("#llm-message");
    let llmControlsInitialized = false;
    let llmOptionsLoading = false;
    let lastServerMessages = [];
    let pendingMessages = [];
    let composerLocked = false;
    let cancelRequestInFlight = false;
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
    let conversationRestartTimer = null;
    let conversationDiscard = false;
    let lastSpokenAssistantMessageId = null;
    let webTtsPlaying = false;
    let thinkingAudio = null;
    let thinkingAudioUrl = "";
    let thinkingAudioPlaying = false;

    function escapeHtml(value) {
      return String(value || "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }

    function ledClass(status) {
      const value = String(status || "unknown").toLowerCase();
      if (["online", "initialized", "ready", "ok", "configured"].includes(value)) return "ok";
      if (["initializing", "reload", "unknown"].includes(value)) return "warn";
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
      return `<div class="message-row ${role}${pending}">
        <div class="bubble">${escapeHtml(message.text)}</div>
      </div>`;
    }

    function sessionButton(session, activeId) {
      const active = session.id === activeId ? " active" : "";
      const count = Number(session.message_count || 0);
      return `<button class="session-item${active}" type="button" data-session-id="${escapeHtml(session.id)}">
        <span class="session-title">${escapeHtml(session.title || "Untitled session")}</span>
        <span class="session-meta">${count} message${count === 1 ? "" : "s"}</span>
      </button>`;
    }

    function renderSessions(sessionContext) {
      const context = sessionContext || {};
      const sessions = context.sessions || [];
      const activeId = context.active_id || "";
      if (sessions.length === 0) {
        sessionList.innerHTML = `<div class="session-meta">No session</div>`;
        return;
      }
      sessionList.innerHTML = sessions.map((session) => sessionButton(session, activeId)).join("");
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
      injectCommand.disabled = composerLocked;
      injectSubmit.disabled = cancelRequestInFlight;
      webConversation.disabled = !webAudio.stt_enabled;
      webMic.disabled = composerLocked || !webAudio.stt_enabled || isRecording || conversationEnabled;
      injectSubmit.classList.toggle("stop-mode", composerLocked);
      injectSubmit.innerHTML = composerLocked ? "&#9632;" : "&#8593;";
      injectSubmit.title = composerLocked ? "Stop" : "Send";
      injectSubmit.setAttribute("aria-label", composerLocked ? "Stop" : "Send");
      injectCommand.placeholder = composerLocked ? "Assistant is thinking..." : "Message";
      if (wasLocked && !composerLocked && !settingsOverlay.classList.contains("open")) {
        window.setTimeout(() => injectCommand.focus({ preventScroll: true }), 0);
      }
    }

    function setRecording(recording) {
      isRecording = Boolean(recording);
      webMic.classList.toggle("recording", isRecording);
      webMic.innerHTML = isRecording ? "&#9632;" : "&#9679;";
      webMic.title = isRecording ? "Stop recording" : "Voice input";
      webMic.setAttribute("aria-label", isRecording ? "Stop recording" : "Voice input");
      webMic.disabled = (composerLocked && !isRecording) || !webAudio.stt_enabled || conversationEnabled;
      injectCommand.placeholder = isRecording ? "Recording..." : (composerLocked ? "Assistant is thinking..." : "Message");
    }

    function updateConversationButton() {
      webConversation.classList.toggle("active", conversationEnabled);
      webConversation.title = conversationEnabled ? "Stop conversation mode" : "Conversation mode";
      webConversation.setAttribute("aria-label", conversationEnabled ? "Stop conversation mode" : "Conversation mode");
      webConversation.disabled = !webAudio.stt_enabled;
      webMic.disabled = (composerLocked && !isRecording) || !webAudio.stt_enabled || conversationEnabled;
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

    function stopMediaStream() {
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

    async function submitCommand(command) {
      const cleanedCommand = command.trim();
      if (!cleanedCommand || composerLocked) return;

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
          await submitCommand(text);
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
      if (!webAudio.stt_enabled || composerLocked || isRecording) return;
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
        mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
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
          stopMediaStream();
          injectCommand.placeholder = "Transcribing...";
          handleRecordedAudio(blob);
        });
        mediaRecorder.start();
        setRecording(true);
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
      const threshold = Number(webAudio.conversation_threshold || 0.035);
      const silenceMs = Math.max(250, Number(webAudio.conversation_silence_ms || 900));

      if (rms > threshold) {
        recordingSpeechDetected = true;
        recordingSilenceStartedAt = null;
      } else if (recordingSpeechDetected) {
        if (!recordingSilenceStartedAt) recordingSilenceStartedAt = now;
        if (now - recordingSilenceStartedAt >= silenceMs) {
          stopWebRecording();
          return;
        }
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

    function stopConversationStream() {
      if (conversationMonitorId) {
        window.cancelAnimationFrame(conversationMonitorId);
        conversationMonitorId = null;
      }
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

    function stopConversationRecording(discard = false) {
      conversationDiscard = discard;
      if (conversationRecorder && conversationRecorder.state !== "inactive") {
        conversationRecorder.stop();
      } else {
        stopConversationStream();
      }
    }

    async function startConversationListening() {
      if (!conversationEnabled || composerLocked || webTtsPlaying || !webAudio.stt_enabled || conversationRecorder) return;
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder || !AudioContextClass) {
        metaEl.textContent = "conversation mode unavailable in this browser";
        conversationEnabled = false;
        updateConversationButton();
        return;
      }

      try {
        conversationChunks = [];
        conversationSpeechDetected = false;
        conversationSilenceStartedAt = null;
        conversationDiscard = false;
        conversationStartedAt = Date.now();
        conversationStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        conversationAudioContext = new AudioContextClass();
        const source = conversationAudioContext.createMediaStreamSource(conversationStream);
        conversationAnalyser = conversationAudioContext.createAnalyser();
        conversationAnalyser.fftSize = 2048;
        source.connect(conversationAnalyser);
        conversationRecorder = new MediaRecorder(conversationStream);
        conversationRecorder.addEventListener("dataavailable", (event) => {
          if (event.data && event.data.size > 0) conversationChunks.push(event.data);
        });
        conversationRecorder.addEventListener("stop", () => {
          const shouldDiscard = conversationDiscard || !conversationSpeechDetected;
          const blob = new Blob(conversationChunks, { type: conversationRecorder.mimeType || "audio/webm" });
          stopConversationStream();
          if (!shouldDiscard) {
            handleRecordedAudio(blob, { applyWakeWord: true, conversation: true }).finally(() => {
              scheduleConversationRestart();
            });
          } else {
            scheduleConversationRestart();
          }
        });
        conversationRecorder.start();
        monitorConversationAudio();
        metaEl.textContent = "conversation listening...";
      } catch (error) {
        stopConversationStream();
        metaEl.textContent = `conversation microphone unavailable: ${error}`;
        scheduleConversationRestart(1500);
      }
    }

    function monitorConversationAudio() {
      if (!conversationRecorder || conversationRecorder.state === "inactive") return;
      const now = Date.now();
      const rms = conversationRms();
      const threshold = Number(webAudio.conversation_threshold || 0.035);
      const silenceMs = Math.max(250, Number(webAudio.conversation_silence_ms || 900));
      const maxRecordMs = Math.max(1000, Number(webAudio.max_record_seconds || 8) * 1000);
      const maxIdleMs = Math.max(3000, Number(webAudio.conversation_idle_seconds || 25) * 1000);

      if (rms > threshold) {
        conversationSpeechDetected = true;
        conversationSilenceStartedAt = null;
      } else if (conversationSpeechDetected) {
        if (!conversationSilenceStartedAt) conversationSilenceStartedAt = now;
        if (now - conversationSilenceStartedAt >= silenceMs) {
          stopConversationRecording(false);
          return;
        }
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
      if (composerLocked || webTtsPlaying) return;
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
        stopConversationRecording(true);
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
        if (!response.ok) throw new Error(await response.text());
        const data = await response.json();
        if (!data.audio_base64 || !data.mime_type) return;
        const audio = new Audio(`data:${data.mime_type};base64,${data.audio_base64}`);
        audio.playbackRate = Math.max(0.6, Math.min(1.8, Number(webAudio.tts_speed || 1)));
        await new Promise((resolve, reject) => {
          audio.addEventListener("ended", resolve, { once: true });
          audio.addEventListener("error", reject, { once: true });
          audio.play().catch(reject);
        });
      } catch (error) {
        metaEl.textContent = `web TTS unavailable: ${error}`;
      } finally {
        webTtsPlaying = false;
        scheduleConversationRestart(250);
      }
    }

    async function startThinkingAudio() {
      if (!thinkingAudioUrl || thinkingAudioPlaying) return;
      try {
        if (!thinkingAudio || thinkingAudio.src !== new URL(thinkingAudioUrl, window.location.href).href) {
          thinkingAudio = new Audio(thinkingAudioUrl);
          thinkingAudio.loop = true;
          thinkingAudio.volume = 0.75;
        }
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

    async function cancelCommand() {
      if (!composerLocked || cancelRequestInFlight) return;
      cancelRequestInFlight = true;
      injectSubmit.disabled = true;
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

      const rows = [...(serverMessages || []), ...pendingMessages];
      const shouldStick = chatScroll.scrollTop + chatScroll.clientHeight >= chatScroll.scrollHeight - 24;
      if (rows.length === 0) {
        messagesEl.innerHTML = `<div class="empty-state">Live Stage Assistant</div>`;
      } else {
        messagesEl.innerHTML = rows.map(messageBubble).join("") + (showThinking ? thinkingBubble() : "");
      }
      if (shouldStick) {
        chatScroll.scrollTop = chatScroll.scrollHeight;
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

    function syncOpenAiSpeedLabel() {
      openaiTtsSpeedLabel.textContent = `${Number(openaiTtsSpeed.value || 1).toFixed(2)}x`;
    }

    function syncTtsProviderControls() {
      const provider = cloudTtsProvider.value || "none";
      const output = selectedTtsOutput();
      const forceSilent = provider === "none";
      for (const input of ttsOutputInputs) {
        input.disabled = forceSilent && input.value !== "silent";
        input.checked = forceSilent ? input.value === "silent" : input.value === output;
      }
      elevenlabsVoiceField.classList.toggle("hidden", provider !== "elevenlabs");
      openaiTtsVoiceField.classList.toggle("hidden", provider !== "openai");
      ttsSpeedField.classList.toggle("hidden", provider === "none");
      elevenlabsVoice.disabled = provider !== "elevenlabs" || elevenlabsVoice.options.length === 0 || !elevenlabsVoice.value;
      openaiTtsVoice.disabled = provider !== "openai" || openaiTtsVoice.options.length === 0 || !openaiTtsVoice.value;
      openaiTtsSpeed.disabled = provider === "none";
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

    async function loadLlmOptions(provider, preferredModel) {
      if (llmOptionsLoading) return;
      llmOptionsLoading = true;
      llmProvider.disabled = true;
      llmModel.disabled = true;
      sessionContextSize.disabled = true;
      wakeWord.disabled = true;
      assistantSystemPromptEl.disabled = true;
      cloudTtsProvider.disabled = true;
      for (const input of ttsOutputInputs) input.disabled = true;
      elevenlabsVoice.disabled = true;
      openaiTtsVoice.disabled = true;
      openaiTtsSpeed.disabled = true;
      thinkingSound.disabled = true;
      llmSave.disabled = true;
      llmMessage.textContent = "Loading LLM options...";
      try {
        const suffix = provider ? `?provider=${encodeURIComponent(provider)}` : "";
        const response = await fetch(`/api/llm-options${suffix}`, { cache: "no-store" });
        if (!response.ok) throw new Error(await response.text());
        const data = await response.json();

        const selectedProvider = data.provider || provider || "";
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
        wakeWord.value = data.selected_wake_word || "";
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
        syncTtsProviderControls();

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
      } catch (error) {
        llmMessage.textContent = `LLM options unavailable: ${error}`;
      } finally {
        llmProvider.disabled = false;
        llmModel.disabled = llmModel.options.length === 0 || !llmModel.value;
        sessionContextSize.disabled = false;
        wakeWord.disabled = false;
        assistantSystemPromptEl.disabled = false;
        cloudTtsProvider.disabled = cloudTtsProvider.options.length === 0 || !cloudTtsProvider.value;
        for (const input of ttsOutputInputs) input.disabled = false;
        syncTtsProviderControls();
        thinkingSound.disabled = thinkingSound.options.length === 0 || !thinkingSound.value;
        llmSave.disabled = !llmProvider.value;
        llmOptionsLoading = false;
      }
    }

    function syncLlmControls(data) {
      if (llmControlsInitialized) return;
      const env = (data.config && data.config.env) || {};
      const provider = String(env.LLM_PROVIDER || "openai").toLowerCase();
      const model = String(env.OPENAI_MODEL || "");
      llmControlsInitialized = true;
      loadLlmOptions(provider, model);
    }

    async function refresh() {
      try {
        const response = await fetch("/api/snapshot", { cache: "no-store" });
        const data = await response.json();
        const previousBusy = composerLocked;
        const services = data.services || {};
        const rows = [
          tile("Internet", data.internet, data.mode === "auto" ? "auto profile detection" : "fixed profile"),
          tile("Profile", data.mode, data.env_file || ""),
          ...Object.entries(services).map(([name, service]) => tile(name, service.status, service.detail))
        ];
        stateEl.innerHTML = rows.join("");
        configEl.value = data.config_text || "";
        syncLlmControls(data);
        promptEl.value = data.prompt || "";
        renderSessions(data.session_context || {});
        if (!settingsOverlay.classList.contains("open")) {
          setSessionContextSize(data.session_context_size || 0);
        }
        const shouldStick = logsEl.scrollTop + logsEl.clientHeight >= logsEl.scrollHeight - 8;
        logsEl.value = data.logs || "";
        if (shouldStick) logsEl.scrollTop = logsEl.scrollHeight;
        webAudio = data.web_audio || { enabled: false, stt_enabled: false, tts_enabled: false };
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
        if (
          previousBusy &&
          !showThinking &&
          webAudio.tts_enabled &&
          latestAssistantMessage &&
          latestAssistantMessage.id !== lastSpokenAssistantMessageId
        ) {
          lastSpokenAssistantMessageId = latestAssistantMessage.id;
          playWebTts(latestAssistantMessage.text || "");
        } else if (previousBusy && !showThinking && conversationEnabled) {
          const delay = webAudio.tts_blocked_by_backend && latestAssistantMessage
            ? Math.min(10000, 1200 + String(latestAssistantMessage.text || "").length * 55)
            : 250;
          scheduleConversationRestart(delay);
        }
        const updated = data.updated_at ? new Date(data.updated_at * 1000).toLocaleTimeString() : "unknown";
        metaEl.textContent = `updated ${updated} · uptime ${data.uptime_seconds || 0}s`;
      } catch (error) {
        metaEl.textContent = `disconnected: ${error}`;
      }
    }

    refresh();
    setInterval(refresh, 1500);

    injectCommand.addEventListener("input", autoSizeComposer);
    injectCommand.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        if (!composerLocked) injectForm.requestSubmit();
      }
    });

    injectForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (composerLocked) {
        await cancelCommand();
        return;
      }
      const command = injectCommand.value.trim();
      if (!command) return;
      await submitCommand(command);
    });

    injectSubmit.addEventListener("click", async (event) => {
      if (!composerLocked) return;
      event.preventDefault();
      await cancelCommand();
    });

    webMic.addEventListener("click", async () => {
      if (isRecording) {
        stopWebRecording();
      } else {
        await startWebRecording();
      }
    });

    webConversation.addEventListener("click", () => {
      setConversationEnabled(!conversationEnabled);
    });

    sessionNew.addEventListener("click", async () => {
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
      }
    });

    sessionList.addEventListener("click", async (event) => {
      const button = event.target.closest(".session-item");
      if (!button || composerLocked) return;
      const sessionId = button.dataset.sessionId;
      if (!sessionId) return;
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
      loadLlmOptions(llmProvider.value, "");
    });

    cloudTtsProvider.addEventListener("change", syncTtsProviderControls);
    for (const input of ttsOutputInputs) {
      input.addEventListener("change", syncTtsProviderControls);
    }
    sessionContextSize.addEventListener("input", syncSessionContextSizeLabel);

    llmSave.addEventListener("click", async () => {
      const provider = llmProvider.value;
      const model = llmModel.value;
      const sessionContextSizeValue = Number(sessionContextSize.value || 0);
      const wakeWordValue = wakeWord.value.trim();
      const systemPromptValue = assistantSystemPromptEl.value.trim();
      const cloudTtsProviderValue = cloudTtsProvider.value;
      const ttsOutputValue = selectedTtsOutput();
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
            cloud_tts_provider: cloudTtsProviderValue,
            tts_output: ttsOutputValue,
            wake_word: wakeWordValue,
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
        await refresh();
      } catch (error) {
        llmMessage.textContent = `Save failed: ${error}`;
      } finally {
        llmSave.disabled = !llmProvider.value;
      }
    });

    openaiTtsSpeed.addEventListener("input", syncOpenAiSpeedLabel);
  </script>
</body>
</html>
"""
