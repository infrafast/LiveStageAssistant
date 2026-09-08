"""JSON-line command/event channel between the runtime parent and engine children.

The parent owns the WebMonitor and sends text commands to the active child over
stdin. Children emit provider-neutral chat/busy events on stdout. This keeps the
GUI/session contract in one place while allowing Classic, Local and Realtime
engines to remain supervised child processes.
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


COMMAND_ENV = "LSA_CHILD_COMMAND_STDIN"
EVENT_PREFIX = "LSA_CHILD_EVENT "


def enabled() -> bool:
    return str(os.getenv(COMMAND_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}


def emit_child_event(payload: dict[str, Any]) -> None:
    try:
        print(EVENT_PREFIX + json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)
    except Exception:
        pass


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
            command = str(payload.get("text") or payload.get("command") or "").strip()
            if not command:
                continue
            self._commands.put(
                {
                    "text": command,
                    "speaker": str(payload.get("speaker") or "unknown").strip() or "unknown",
                    "speaker_confidence": float(payload.get("speaker_confidence") or 0.0),
                    "speaker_backend": str(payload.get("speaker_backend") or "none").strip() or "none",
                    "speaker_second_confidence": float(payload.get("speaker_second_confidence") or 0.0),
                    "speaker_reason": str(payload.get("speaker_reason") or "").strip(),
                    "speaker_candidates": payload.get("speaker_candidates") if isinstance(payload.get("speaker_candidates"), list) else [],
                    "session_id": str(payload.get("session_id") or "").strip(),
                }
            )

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


class ChildCommandMonitor:
    """Small WebMonitor-compatible adapter used inside supervised child engines."""

    def __init__(self, reader: StdinCommandReader | None = None) -> None:
        self.reader = reader or StdinCommandReader()
        self.reader.start()
        self._messages: deque[dict[str, Any]] = deque(maxlen=80)
        self._next_message_id = 1
        self._assistant_busy = False

    def pop_injected_command(self) -> dict[str, Any] | None:
        return self.reader.pop_command()

    def request_cancel(self) -> None:
        self.reader._cancel_requested.set()

    def pop_cancel_requested(self) -> bool:
        return self.reader.pop_cancel_requested()

    def append_dialogue(self, role: str, text: str, *, speak: bool = False) -> None:
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
        emit_child_event({
            "type": "message",
            "role": normalized_role,
            "text": cleaned,
            "speak": bool(speak),
            "persisted_by_child": True,
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
        emit_child_event({"type": "busy", "assistant_busy": busy})

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
    if not enabled():
        return None
    return ChildCommandMonitor()
