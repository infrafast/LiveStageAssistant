"""Web monitor compatibility wrapper with the RV2D realtime-policy endpoint.

The historical monitor implementation is kept byte-for-byte in
``web_monitor_base.py``. This thin layer only adds the generic MCP realtime
policy POST endpoint so RV2D can evolve without duplicating the existing web
monitor.
"""

from __future__ import annotations

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


class WebMonitor(_BaseWebMonitor):
    """Historical WebMonitor plus one generic MCP realtime-policy save route."""

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

    def start(self, host: str = "127.0.0.1", port: int = 8765) -> tuple[str, int]:
        monitor = self
        with _START_PATCH_LOCK:
            original_http_server = _base.ThreadingHTTPServer

            def server_factory(server_address, handler_class):
                class RealtimePolicyHandler(handler_class):
                    def do_POST(self) -> None:
                        parsed = _base.urlparse(self.path)
                        if parsed.path != "/api/mcp-realtime-policy":
                            super().do_POST()
                            return
                        if self._auth_required(parsed.path):
                            self._send_auth_required()
                            return
                        self._handle_mcp_realtime_policy_save()

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
