"""Engine-neutral services used by the runtime-owned WebMonitor.

The production HTTP server belongs to ``voice_assistant.runtime``.  This module
contains file/session configuration services only; it does not bind sockets and
has no Classic/Realtime/Local engine dependency.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any, Callable
from urllib.parse import urlparse

from dotenv import dotenv_values

from .session_context import DEFAULT_CONTEXT_DIR, DEFAULT_SUMMARY_MAX_CHARS, SessionContextStore


class RuntimeWebServices:
    """Common mutable services behind the single runtime WebMonitor."""

    def __init__(
        self,
        *,
        monitor: Any,
        active_profile: Callable[[], Path],
        automatic_profiles: bool = True,
        request_reload: Callable[[], None] | None = None,
    ) -> None:
        self.monitor = monitor
        self.active_profile = active_profile
        self.automatic_profiles = bool(automatic_profiles)
        self.request_reload = request_reload
        self._lock = threading.RLock()

    def bind(self) -> None:
        """Register only engine-neutral handlers on the common WebMonitor."""
        self.monitor.set_env_profile_handlers(
            list_handler=self.list_env_profiles,
            switch_handler=self.switch_env_profile,
        )
        self.monitor.set_remote_screen_handler(self.save_remote_screen)
        self.monitor.set_mcp_routing_save_handler(self.save_mcp_routing)
        self.monitor.set_mcp_server_options_save_handler(self.save_mcp_server_options)
        self.monitor.set_session_context_handlers(
            list_handler=self.session_snapshot,
            new_handler=self.new_session,
            select_handler=self.select_session,
            rename_handler=self.rename_session,
            clear_handler=self.clear_session,
            save_handler=self.save_session,
            delete_handler=self.delete_session,
        )

    def _values(self, profile: Path | None = None) -> dict[str, Any]:
        return dict(dotenv_values(profile or self.active_profile()))

    def _profile_dir(self) -> Path:
        return self.active_profile().parent

    def _write_env(self, profile: Path, updates: dict[str, str]) -> None:
        if not profile.is_file():
            raise ValueError(f"env file not found: {profile}")
        original_mode = profile.stat().st_mode & 0o777
        pending = {str(key): str(value) for key, value in updates.items()}
        output: list[str] = []
        for line in profile.read_text(encoding="utf-8").splitlines():
            key, sep, _value = line.partition("=")
            if sep and key.strip() in pending:
                clean_key = key.strip()
                output.append(f"{clean_key}={self._format_env_value(pending.pop(clean_key))}")
            else:
                output.append(line)
        output.extend(f"{key}={self._format_env_value(value)}" for key, value in pending.items())
        profile.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=profile.parent, delete=False) as handle:
            handle.write("\n".join(output) + "\n")
            temporary = Path(handle.name)
        os.chmod(temporary, original_mode)
        temporary.replace(profile)

    @staticmethod
    def _format_env_value(value: str) -> str:
        text = str(value)
        if re.fullmatch(r"[A-Za-z0-9_./:@+-]*", text):
            return text
        escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
        return f'"{escaped}"'

    def _mcp_path(self, values: dict[str, Any] | None = None) -> Path:
        values = values or self._values()
        configured = str(values.get("MCP_CONFIG") or "mcp_servers.json").strip()
        path = Path(os.path.expandvars(configured)).expanduser()
        if path.is_absolute():
            return path
        candidates = (self.active_profile().parent / path, Path.cwd() / path)
        return next((candidate for candidate in candidates if candidate.is_file()), candidates[-1])

    def _load_mcp(self) -> tuple[Path, dict[str, Any]]:
        path = self._mcp_path()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError as error:
            raise ValueError(f"could not read MCP_CONFIG '{path}': {error}") from error
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSON in MCP_CONFIG '{path}': {error}") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("mcpServers"), dict):
            raise ValueError("active MCP config has no mcpServers object")
        return path, payload

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        original_mode = (path.stat().st_mode & 0o777) if path.exists() else 0o644
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            temporary = Path(handle.name)
        os.chmod(temporary, original_mode)
        temporary.replace(path)

    def _refresh_monitor_config(self, *, mcp_config: dict[str, Any] | None = None) -> None:
        values = self._values()
        self.monitor.set_web_password(str(values.get("WEB_PASSWORD") or "").strip())
        kwargs: dict[str, Any] = {"env_file": self.active_profile(), "env_values": values}
        if mcp_config is not None:
            kwargs["mcp_config"] = mcp_config
        self.monitor.update(**kwargs)

    def _reload(self) -> None:
        if self.request_reload is not None:
            self.request_reload()

    def list_env_profiles(self) -> dict[str, Any]:
        active = self.active_profile().resolve()
        profiles = []
        for path in (self._profile_dir() / ".env.online", self._profile_dir() / ".env.offline"):
            if not path.is_file():
                continue
            profiles.append({"id": str(path), "label": path.name, "selected": path.resolve() == active})
        return {
            "current": str(active),
            "profiles": profiles,
            "switching_enabled": not self.automatic_profiles,
            "auto_mode": self.automatic_profiles,
            "connectivity_locked": self.automatic_profiles,
            "message": "Connectivity selects the active profile automatically." if self.automatic_profiles else "",
        }

    def switch_env_profile(self, selection: str) -> dict[str, Any]:
        if self.automatic_profiles:
            raise ValueError("manual env switching is disabled while the common runtime controls connectivity")
        candidate = Path(selection).expanduser().resolve()
        allowed = {path.resolve() for path in self._profile_dir().glob(".env*") if path.is_file()}
        if candidate not in allowed:
            raise ValueError("selected env file is outside the available profiles")
        raise ValueError("fixed-profile switching must be requested through the runtime supervisor")

    def save_remote_screen(self, vnc_url: str, view_only: bool) -> dict[str, Any]:
        cleaned = str(vnc_url or "").strip()
        parsed = urlparse(cleaned)
        if parsed.scheme not in {"vnc", "http", "https"} or not parsed.netloc:
            raise ValueError("VNC URL must include vnc://, http://, or https:// and a host")
        with self._lock:
            self._write_env(
                self.active_profile(),
                {
                    "REMOTE_SCREEN_VNC_URL": cleaned,
                    "REMOTE_SCREEN_VNC_VIEW_ONLY": "true" if view_only else "false",
                },
            )
            self._refresh_monitor_config()
        return {"saved": True, "vnc_url": cleaned, "view_only": bool(view_only)}

    @staticmethod
    def _routing_words(value: Any) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in re.split(r"[,;\n]+", str(value or "")):
            word = item.strip().lower()
            if word and word not in seen:
                result.append(word)
                seen.add(word)
        return result

    def save_mcp_routing(self, updates: dict[str, str]) -> dict[str, Any]:
        with self._lock:
            path, config = self._load_mcp()
            servers = config["mcpServers"]
            unknown = sorted(name for name in updates if name not in servers)
            if unknown:
                raise ValueError(f"unknown MCP server(s): {', '.join(unknown)}")
            owner: dict[str, str] = {}
            normalized: dict[str, str] = {}
            for server_name, server_config in servers.items():
                if not isinstance(server_config, dict):
                    continue
                options = server_config.get("assistantOptions")
                if not isinstance(options, dict):
                    options = {}
                    server_config["assistantOptions"] = options
                source = updates.get(str(server_name), options.get("routing", ""))
                words = self._routing_words(source)
                if len(words) > 10:
                    raise ValueError(f"routing words limit exceeded: {server_name} has {len(words)} words, max 10")
                for word in words:
                    previous = owner.get(word)
                    if previous and previous != server_name:
                        raise ValueError(f"routing word duplicate: {word}")
                    owner[word] = str(server_name)
                normalized[str(server_name)] = ",".join(words)
                options["routing"] = normalized[str(server_name)]
            self._write_json_atomic(path, config)
            self._refresh_monitor_config(mcp_config=config)
            self._reload()
        return {"ok": True, "mcp_config": str(path), "routing": normalized, "restart_required": True}

    @staticmethod
    def _normalized_env_options(value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            raise ValueError("MCP server options must be JSON objects")
        result: dict[str, str] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key).strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise ValueError(f"invalid MCP env option name: {key}")
            if isinstance(raw_value, (dict, list)):
                result[key] = json.dumps(raw_value, ensure_ascii=False, separators=(",", ":"))
            elif isinstance(raw_value, bool):
                result[key] = "true" if raw_value else "false"
            elif raw_value is None:
                result[key] = ""
            else:
                result[key] = str(raw_value)
        return result

    def save_mcp_server_options(self, updates: dict[str, dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            path, config = self._load_mcp()
            servers = config["mcpServers"]
            unknown = sorted(name for name in updates if name not in servers)
            if unknown:
                raise ValueError(f"unknown MCP server(s): {', '.join(unknown)}")
            normalized: dict[str, dict[str, str]] = {}
            for server_name, raw in updates.items():
                normalized[str(server_name)] = self._normalized_env_options(raw)
                server = servers.get(server_name)
                if isinstance(server, dict):
                    server["env"] = normalized[str(server_name)]
            self._write_json_atomic(path, config)
            self._refresh_monitor_config(mcp_config=config)
            self._reload()
        return {"ok": True, "mcp_config": str(path), "options": normalized, "restart_required": True}

    def _session_store(self) -> SessionContextStore:
        values = self._values()
        root = str(values.get("SESSION_CONTEXT_DIR") or DEFAULT_CONTEXT_DIR).strip() or str(DEFAULT_CONTEXT_DIR)
        return SessionContextStore(root, summary_max_chars=DEFAULT_SUMMARY_MAX_CHARS)

    def _publish_session(self, store: SessionContextStore) -> dict[str, Any]:
        snapshot = store.snapshot()
        self.monitor.replace_dialogue(snapshot.get("messages") or [])
        size = int(str(self._values().get("SESSION_CONTEXT_SIZE") or "6000"))
        self.monitor.set_context_state(snapshot, session_context_size=size)
        return snapshot

    def session_snapshot(self) -> dict[str, Any]:
        return self._publish_session(self._session_store())

    def new_session(self, title: str | None = None) -> dict[str, Any]:
        store = self._session_store()
        store.new_session(title)
        return self._publish_session(store)

    def select_session(self, session_id: str) -> dict[str, Any]:
        store = self._session_store()
        store.select_session(session_id)
        return self._publish_session(store)

    def rename_session(self, session_id: str, title: str) -> dict[str, Any]:
        store = self._session_store()
        store.rename_session(session_id, title)
        return self._publish_session(store)

    def clear_session(self, session_id: str) -> dict[str, Any]:
        store = self._session_store()
        store.clear_session_conversation(session_id, preserve_llm_summary=True)
        return self._publish_session(store)

    def save_session(self, session_id: str) -> dict[str, Any]:
        # Persisted messages/summary are already written on each mutation. LLM
        # summary refresh remains an engine service and is intentionally not
        # performed by the HTTP runtime.
        store = self._session_store()
        store.select_session(session_id)
        snapshot = self._publish_session(store)
        snapshot["llm_summary_refreshed"] = False
        return snapshot

    def delete_session(self, session_id: str) -> dict[str, Any]:
        store = self._session_store()
        store.delete_session(session_id)
        return self._publish_session(store)
