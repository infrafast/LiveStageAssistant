"""Command/event channel between the runtime parent and engine children.

The parent owns the WebMonitor. Supervised child engines consume composer
commands from the parent and publish chat/busy events back to the same
WebMonitor contract. The default supervised path uses local loopback HTTP so no
browser-facing LAN/Tailscale URL or base path is involved.
"""

from __future__ import annotations

from collections import deque
import json
import os
import queue
import sys
import threading
import time
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request


STDIN_ENV = "LSA_CHILD_COMMAND_STDIN"
HTTP_ENV = "LSA_CHILD_COMMAND_HTTP"
HTTP_URL_ENV = "LSA_PARENT_WEB_URL"
EVENT_PREFIX = "LSA_CHILD_EVENT "


def env_enabled(name: str, default: bool = False) -> bool:
    return str(os.getenv(name) or ("1" if default else "")).strip().lower() in {"1", "true", "yes", "on"}


def emit_child_event(payload: dict[str, Any]) -> None:
    try:
        print(EVENT_PREFIX + json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)
    except Exception:
        pass


def parent_web_url() -> str:
    configured = str(os.getenv(HTTP_URL_ENV) or "").strip().rstrip("/")
    if configured:
        return configured
    host = str(os.getenv("WEB_MONITOR_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    if host in {"0.0.0.0", "::", ""}:
        host = "127.0.0.1"
    port = str(os.getenv("WEB_MONITOR_PORT") or "8765").strip() or "8765"
    return f"http://{host}:{port}"


def post_json(url: str, payload: dict[str, Any], *, timeout: float = 0.8) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = urllib_request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib_request.urlopen(req, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    parsed = json.loads(body or "{}")
    return parsed if isinstance(parsed, dict) else {}


class StdinCommandReader:
    """Background JSON-line reader for commands sent by the parent runtime."""

    def __init__(self) -> None:
        self._commands: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._cancel_requested = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="lsa-child-command-stdin", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                emit_child_event({"type": "error", "message": f"invalid child command JSON: {exc}"})
                continue
            if not isinstance(payload, dict):
                continue
            kind = str(payload.get("type") or "command").strip().lower()
            if kind == "cancel":
                self._cancel_requested.set()
                continue
            if kind != "command":
                continue
            command = normalize_command_payload(payload)
            if command:
                self._commands.put(command)

    def pop_command(self) -> dict[str, Any] | None:
        try:
            return self._commands.get_nowait()
        except queue.Empty:
            return None

    def pop_cancel_requested(self) -> bool:
        if not self._cancel_requested.is_set():
            return False
        self._cancel_requested.clear()
        return True


def normalize_command_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    command = str(payload.get("text") or payload.get("command") or "").strip()
    if not command:
        return None
    return {
        "text": command,
        "speaker": str(payload.get("speaker") or "unknown").strip() or "unknown",
        "speaker_confidence": float(payload.get("speaker_confidence") or 0.0),
        "speaker_backend": str(payload.get("speaker_backend") or "none").strip() or "none",
        "speaker_second_confidence": float(payload.get("speaker_second_confidence") or 0.0),
        "speaker_reason": str(payload.get("speaker_reason") or "").strip(),
        "speaker_candidates": payload.get("speaker_candidates") if isinstance(payload.get("speaker_candidates"), list) else [],
        "session_id": str(payload.get("session_id") or "").strip(),
    }


class LocalHttpCommandClient:
    """Loopback client used by supervised child engines."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or parent_web_url()).rstrip("/")
        self._cancel_requested = False

    def pop_command(self) -> dict[str, Any] | None:
        try:
            data = post_json(f"{self.base_url}/api/child-command-next", {}, timeout=0.9)
        except (OSError, urllib_error.URLError, TimeoutError, json.JSONDecodeError):
            return None
        if data.get("cancel"):
            self._cancel_requested = True
        command = data.get("command")
        if isinstance(command, dict):
            return normalize_command_payload(command)
        return None

    def pop_cancel_requested(self) -> bool:
        if self._cancel_requested:
            self._cancel_requested = False
            return True
        try:
            data = post_json(f"{self.base_url}/api/child-command-next", {"command": False}, timeout=0.9)
        except (OSError, urllib_error.URLError, TimeoutError, json.JSONDecodeError):
            return False
        return bool(data.get("cancel"))

    def send_event(self, payload: dict[str, Any]) -> None:
        try:
            post_json(f"{self.base_url}/api/child-event", payload, timeout=0.9)
        except (OSError, urllib_error.URLError, TimeoutError, json.JSONDecodeError):
            emit_child_event({"type": "error", "message": "could not deliver child event to parent WebMonitor"})


class ChildCommandMonitor:
    """Small WebMonitor-compatible adapter used inside supervised child engines."""

    def __init__(self, reader: StdinCommandReader | LocalHttpCommandClient | None = None) -> None:
        self.reader = reader or StdinCommandReader()
        if isinstance(self.reader, StdinCommandReader):
            self.reader.start()
        self._messages: deque[dict[str, Any]] = deque(maxlen=80)
        self._next_message_id = 1
        self._assistant_busy = False

    def _send_event(self, payload: dict[str, Any]) -> None:
        if isinstance(self.reader, LocalHttpCommandClient):
            self.reader.send_event(payload)
        emit_child_event(payload)

    def pop_injected_command(self) -> dict[str, Any] | None:
        return self.reader.pop_command()

    def request_cancel(self) -> None:
        if isinstance(self.reader, StdinCommandReader):
            self.reader._cancel_requested.set()

    def pop_cancel_requested(self) -> bool:
        return self.reader.pop_cancel_requested()

    def append_dialogue(self, role: str, text: str, *, speak: bool = False, persisted_by_child: bool = True) -> None:
        cleaned = str(text or "").strip()
        if not cleaned:
            return
        normalized_role = role if role in {"user", "assistant"} else "assistant"
        message = {
            "id": self._next_message_id,
            "role": normalized_role,
            "text": cleaned,
            "speak": bool(speak),
            "created_at": time.time(),
        }
        self._next_message_id += 1
        self._messages.append(message)
        self._send_event({
            "type": "message",
            "role": normalized_role,
            "text": cleaned,
            "speak": bool(speak),
            "persisted_by_child": bool(persisted_by_child),
        })

    def replace_dialogue(self, messages: list[dict[str, Any]]) -> None:
        self._messages.clear()
        next_id = 1
        for message in messages[-80:]:
            text = str(message.get("text") or "").strip()
            if not text:
                continue
            message_id = int(message.get("id") or next_id)
            self._messages.append({
                "id": message_id,
                "role": message.get("role") if message.get("role") in {"user", "assistant"} else "assistant",
                "text": text,
                "speak": bool(message.get("speak")),
                "created_at": float(message.get("created_at") or time.time()),
            })
            next_id = max(next_id, message_id + 1)
        self._next_message_id = next_id

    def set_context_state(self, *_args: Any, **_kwargs: Any) -> None:
        return

    def set_assistant_busy(self, busy: bool) -> None:
        busy = bool(busy)
        if self._assistant_busy == busy:
            return
        self._assistant_busy = busy
        self._send_event({"type": "busy", "assistant_busy": busy})

    def set_environment_loading(self, *_args: Any, **_kwargs: Any) -> None:
        return

    def update(self, *_args: Any, **_kwargs: Any) -> None:
        return

    def append_log(self, value: str, source: str = "stdout") -> None:
        if value:
            print(value, end="" if value.endswith("\n") else "\n", flush=True)

    def snapshot(self) -> dict[str, Any]:
        return {"messages": list(self._messages), "assistant_busy": self._assistant_busy}


def child_monitor_from_env() -> ChildCommandMonitor | None:
    if env_enabled(STDIN_ENV):
        return ChildCommandMonitor(StdinCommandReader())
    if env_enabled(HTTP_ENV) or env_enabled("LSA_COMMON_STARTUP_LIFECYCLE"):
        return ChildCommandMonitor(LocalHttpCommandClient())
    return None
