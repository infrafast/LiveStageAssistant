"""Persistent chat session context storage for Live Stage Assistant."""

from __future__ import annotations

import json
from pathlib import Path
import re
import time
from typing import Any
from uuid import uuid4


DEFAULT_CONTEXT_DIR = Path(".contexts")
DEFAULT_SUMMARY_MAX_CHARS = 12000
DEFAULT_MAX_MESSAGES = 200


class SessionContextStore:
    """Store chat sessions as small JSON .context files."""

    def __init__(
        self,
        context_dir: str | Path = DEFAULT_CONTEXT_DIR,
        *,
        summary_max_chars: int = DEFAULT_SUMMARY_MAX_CHARS,
        max_messages: int = DEFAULT_MAX_MESSAGES,
    ) -> None:
        self.context_dir = Path(context_dir)
        self.summary_max_chars = max(500, int(summary_max_chars or DEFAULT_SUMMARY_MAX_CHARS))
        self.max_messages = max(20, int(max_messages or DEFAULT_MAX_MESSAGES))
        self.active_file = self.context_dir / "active_session"
        self.current: dict[str, Any] = {}
        self.context_dir.mkdir(parents=True, exist_ok=True)
        self.load_active_or_latest()

    @property
    def active_id(self) -> str:
        return str(self.current.get("id") or "")

    def _session_path(self, session_id: str) -> Path:
        cleaned_id = re.sub(r"[^A-Za-z0-9_.-]", "", session_id)
        return self.context_dir / f"{cleaned_id}.context.json"

    def _new_session_data(self, title: str | None = None) -> dict[str, Any]:
        now = time.time()
        session_id = time.strftime("%Y%m%d-%H%M%S", time.gmtime(now)) + f"-{uuid4().hex[:8]}"
        return {
            "id": session_id,
            "title": (title or "New session").strip() or "New session",
            "created_at": now,
            "updated_at": now,
            "summary": "",
            "llm_summary": "",
            "llm_summary_updated_at": None,
            "messages": [],
        }

    def _read_session(self, path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or not data.get("id"):
            return None
        data.setdefault("title", "Untitled session")
        data.setdefault("created_at", time.time())
        data.setdefault("updated_at", data["created_at"])
        data.setdefault("summary", "")
        data.setdefault("llm_summary", "")
        data.setdefault("llm_summary_updated_at", None)
        data.setdefault("messages", [])
        return data

    def _write_session(self, data: dict[str, Any], *, set_active: bool = True) -> None:
        self.context_dir.mkdir(parents=True, exist_ok=True)
        path = self._session_path(str(data["id"]))
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        if set_active:
            self.active_file.write_text(str(data["id"]))

    def _save_current(self) -> None:
        if self.current:
            self._write_session(self.current)

    def list_sessions(self) -> list[dict[str, Any]]:
        sessions = []
        for path in self.context_dir.glob("*.context.json"):
            data = self._read_session(path)
            if not data:
                continue
            sessions.append(
                {
                    "id": data["id"],
                    "title": data.get("title") or "Untitled session",
                    "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at"),
                    "message_count": len(data.get("messages") or []),
                    "summary": data.get("summary") or "",
                    "llm_summary": data.get("llm_summary") or "",
                }
            )
        sessions.sort(key=lambda item: float(item.get("updated_at") or 0), reverse=True)
        return sessions

    def load_active_or_latest(self) -> dict[str, Any]:
        active_id = ""
        try:
            active_id = self.active_file.read_text().strip()
        except OSError:
            pass

        if active_id:
            data = self._read_session(self._session_path(active_id))
            if data:
                self.current = data
                return data

        sessions = self.list_sessions()
        if sessions:
            data = self._read_session(self._session_path(str(sessions[0]["id"])))
            if data:
                self.current = data
                self.active_file.write_text(str(data["id"]))
                return data

        self.current = self._new_session_data()
        self._save_current()
        return self.current

    def new_session(self, title: str | None = None) -> dict[str, Any]:
        self.current = self._new_session_data(title)
        self._save_current()
        return self.current

    def select_session(self, session_id: str) -> dict[str, Any]:
        data = self._read_session(self._session_path(session_id))
        if not data:
            raise ValueError(f"session '{session_id}' was not found")
        self.current = data
        self._save_current()
        return self.current

    def rename_session(self, session_id: str, title: str) -> dict[str, Any]:
        cleaned_title = re.sub(r"\s+", " ", title).strip()
        if not cleaned_title:
            raise ValueError("session title cannot be empty")
        data = self._read_session(self._session_path(session_id))
        if not data:
            raise ValueError(f"session '{session_id}' was not found")
        data["title"] = cleaned_title[:120]
        data["updated_at"] = time.time()
        is_active = self.active_id == data["id"]
        if is_active:
            self.current = data
        self._write_session(data, set_active=is_active)
        return data

    def delete_session(self, session_id: str) -> dict[str, Any]:
        path = self._session_path(session_id)
        data = self._read_session(path)
        if not data:
            raise ValueError(f"session '{session_id}' was not found")

        was_active = self.active_id == data["id"]
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        if was_active:
            try:
                self.active_file.unlink()
            except FileNotFoundError:
                pass
            self.current = {}
            return self.load_active_or_latest()
        return self.current or self.load_active_or_latest()

    def clear_current(self) -> None:
        if not self.current:
            self.load_active_or_latest()
        self.current["messages"] = []
        self.current["summary"] = ""
        self.current["llm_summary"] = ""
        self.current["llm_summary_updated_at"] = None
        self.current["updated_at"] = time.time()
        self._save_current()

    def append_message(self, role: str, text: str) -> dict[str, Any] | None:
        cleaned_text = text.strip()
        if not cleaned_text:
            return None
        if not self.current:
            self.load_active_or_latest()

        now = time.time()
        normalized_role = role if role in {"user", "assistant"} else "assistant"
        message = {
            "id": len(self.current.get("messages") or []) + 1,
            "role": normalized_role,
            "text": cleaned_text,
            "created_at": now,
        }
        messages = list(self.current.get("messages") or [])
        messages.append(message)
        if len(messages) > self.max_messages:
            messages = messages[-self.max_messages :]
        self.current["messages"] = messages
        if normalized_role == "user" and self.current.get("title") in {"", "New session", "Untitled session"}:
            self.current["title"] = self._title_from_text(cleaned_text)
        self.current["summary"] = self._build_summary(messages)
        self.current["updated_at"] = now
        self._save_current()
        return message

    def context_text(self, *, exclude_last_user: bool = False, max_chars: int | None = None) -> str:
        uses_llm_summary = bool(self.current) and bool(str(self.current.get("llm_summary") or "").strip())
        summary = self.injectable_summary().strip()
        if max_chars is not None:
            limit = max(0, int(max_chars))
            if limit == 0:
                return ""
            if self.current and not uses_llm_summary:
                messages = list(self.current.get("messages") or [])
                if exclude_last_user and messages and messages[-1].get("role") == "user":
                    messages = messages[:-1]
                summary = self._build_summary(messages, max_chars=limit)
            elif len(summary) > limit:
                summary = summary[: max(0, limit - 3)].rstrip() + "..."
        elif exclude_last_user and self.current:
            messages = list(self.current.get("messages") or [])
            if messages and messages[-1].get("role") == "user":
                summary = self._build_summary(messages[:-1])
        if not summary:
            return ""
        return (
            "Session context summary from the currently selected persisted chat session. "
            "Use this only for conversational continuity, preferences, and follow-up references; "
            "do not treat it as source of truth for live external state:\n"
            f"{summary}"
        )

    def injectable_summary(self) -> str:
        if not self.current:
            return ""
        llm_summary = str(self.current.get("llm_summary") or "").strip()
        if llm_summary:
            return llm_summary
        return str(self.current.get("summary") or "").strip()

    def summary_source_text(self) -> str:
        return str(self.current.get("summary") or "").strip() if self.current else ""

    def set_llm_summary(self, summary: str, source_summary: str | None = None) -> None:
        if not self.current:
            self.load_active_or_latest()
        cleaned_summary = summary.strip()
        self.current["llm_summary"] = cleaned_summary
        self.current["llm_summary_updated_at"] = time.time() if cleaned_summary else None
        self.current["updated_at"] = time.time()
        self._save_current()

    def snapshot(self) -> dict[str, Any]:
        if not self.current:
            self.load_active_or_latest()
        return {
            "active_id": self.active_id,
            "sessions": self.list_sessions(),
            "current": self.current,
            "messages": list(self.current.get("messages") or []),
        }

    def _title_from_text(self, text: str) -> str:
        compact = re.sub(r"\s+", " ", text).strip()
        if len(compact) <= 48:
            return compact or "New session"
        return compact[:45].rstrip() + "..."

    def _build_summary(self, messages: list[dict[str, Any]], *, max_chars: int | None = None) -> str:
        limit = max(0, int(max_chars if max_chars is not None else self.summary_max_chars))
        if limit == 0:
            return ""
        lines = []
        total = 0
        for message in reversed(messages):
            role = "User" if message.get("role") == "user" else "Assistant"
            text = re.sub(r"\s+", " ", str(message.get("text") or "")).strip()
            if not text:
                continue
            line = f"{role}: {text}"
            line_len = len(line) + 1
            if lines and total + line_len > limit:
                break
            if line_len > limit:
                line = line[: max(0, limit - 3)].rstrip() + "..."
                line_len = len(line) + 1
            lines.append(line)
            total += line_len
        lines.reverse()
        return "\n".join(lines)
