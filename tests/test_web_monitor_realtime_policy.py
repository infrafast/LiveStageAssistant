from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib import request as urllib_request

from voice_assistant.web_monitor import WebMonitor, _runtime_service_tiles


class WebMonitorRealtimePolicyRouteTests(unittest.TestCase):
    def test_runtime_service_tiles_are_provider_and_mcp_neutral(self) -> None:
        tiles = _runtime_service_tiles({
            "engine": "openai-realtime", "provider": "openai", "model": "gpt-realtime-2.1", "voice": "marin", "ready": True,
            "mcp": [{"name": "alpha", "configured_transport": "auto", "effective_transport": "stdio", "permission": "open", "healthy": True, "detail": "selected and healthy"}],
        })
        self.assertEqual(tiles["Voice engine"]["status"], "ready")
        self.assertIn("transport=stdio", tiles["MCP · alpha"]["detail"])
        self.assertIn("configured=auto", tiles["MCP · alpha"]["detail"])

    def test_native_transport_is_presented_as_https(self) -> None:
        tiles = _runtime_service_tiles({"engine": "openai-realtime", "ready": True, "mcp": [{"name": "alpha", "configured_transport": "native", "effective_transport": "native", "permission": "open", "healthy": True}]})
        self.assertIn("transport=https", tiles["MCP · alpha"]["detail"])
        self.assertNotIn("transport=native", tiles["MCP · alpha"]["detail"])

    def test_snapshot_includes_runtime_status_tiles_when_status_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            status_path = Path(temp_dir) / "runtime-status.json"
            status_path.write_text(json.dumps({"engine": "classic", "provider": "openai", "model": "gpt-4.1-mini", "ready": True, "mcp": []}), encoding="utf-8")
            with patch.dict(os.environ, {"LSA_RUNTIME_STATUS_FILE": str(status_path)}):
                snapshot = WebMonitor().snapshot()
            self.assertEqual(snapshot["runtime_status"]["engine"], "classic")
            self.assertEqual(snapshot["services"]["Voice engine"]["status"], "ready")

    def test_runtime_http_omits_cross_origin_isolation_headers(self) -> None:
        monitor = WebMonitor(); host, port = monitor.start("127.0.0.1", 0)
        try:
            with urllib_request.urlopen(f"http://{host}:{port}/", timeout=2) as response:
                headers = response.headers; response.read()
        finally:
            monitor.stop()
        self.assertIsNone(headers.get("Cross-Origin-Opener-Policy"))
        self.assertIsNone(headers.get("Cross-Origin-Embedder-Policy"))

    def test_voice_engine_persists_to_canonical_profile_and_requires_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env.online"
            env_path.write_text("CONNECTIVITY_MODE=online\nVOICE_ENGINE=classic\n", encoding="utf-8")
            monitor = WebMonitor(); monitor.update(env_values={"CONNECTIVITY_MODE": "online", "VOICE_ENGINE": "classic"})
            with patch.dict(os.environ, {"ASSISTANT_AUTO_ENV_DIR": temp_dir}):
                result = monitor._save_voice_engine("openai-realtime", realtime_model="gpt-realtime-2.1", realtime_voice="marin")
            saved = env_path.read_text(encoding="utf-8")
            self.assertIn("VOICE_ENGINE=openai-realtime", saved)
            self.assertEqual(result["profile"], str(env_path))
            self.assertTrue(result["restart_required"])

    def test_voice_output_gains_are_persisted_to_online_and_offline_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            online = Path(temp_dir) / ".env.online"; offline = Path(temp_dir) / ".env.offline"
            online.write_text("CONNECTIVITY_MODE=online\n", encoding="utf-8"); offline.write_text("CONNECTIVITY_MODE=offline\n", encoding="utf-8")
            monitor = WebMonitor(); monitor.update(env_values={"CONNECTIVITY_MODE": "online"})
            with patch.dict(os.environ, {"ASSISTANT_AUTO_ENV_DIR": temp_dir}):
                result = monitor._save_voice_output_gains(1.35, 0.80)
            self.assertTrue(result["restart_required"])
            self.assertIn("CLOUD_TTS_OUTPUT_GAIN=1.35", online.read_text(encoding="utf-8"))
            self.assertIn("LOCAL_TTS_OUTPUT_GAIN=0.80", offline.read_text(encoding="utf-8"))

    def test_mcp_crud_route_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "mcp.json"
            config_path.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
            monitor = WebMonitor(); monitor.update(env_file=Path(temp_dir) / ".env.online", env_values={"MCP_CONFIG": str(config_path)}, mcp_config={"mcpServers": {}})
            host, port = monitor.start("127.0.0.1", 0)
            try:
                create = json.dumps({"action": "create", "server": {"name": "alpha", "command": "node", "args": '["server.js"]', "realtime_transport": "stdio", "permission_mode": "open"}}).encode()
                with urllib_request.urlopen(urllib_request.Request(f"http://{host}:{port}/api/mcp-server", data=create, headers={"Content-Type": "application/json"}, method="POST"), timeout=2) as response:
                    result = json.loads(response.read().decode())
                self.assertTrue(result["ok"]); self.assertTrue(result["restart_required"])
                self.assertIn("alpha", json.loads(config_path.read_text(encoding="utf-8"))["mcpServers"])
                delete = json.dumps({"action": "delete", "server": "alpha"}).encode()
                with urllib_request.urlopen(urllib_request.Request(f"http://{host}:{port}/api/mcp-server", data=delete, headers={"Content-Type": "application/json"}, method="POST"), timeout=2) as response:
                    deleted = json.loads(response.read().decode())
                self.assertEqual(deleted["deleted"], "alpha")
            finally:
                monitor.stop()

    def test_policy_route_uses_https_vocabulary_and_preserves_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "mcp.json"
            config_path.write_text(json.dumps({"mcpServers": {"alpha": {"command": "node", "native": {"url": "https://old.example/mcp", "headers": {"Authorization": "Bearer keep-secret"}}, "realtime": {"transport": "stdio", "permissions": {"mode": "open"}}}}}), encoding="utf-8")
            initial = json.loads(config_path.read_text(encoding="utf-8")); monitor = WebMonitor(); monitor.update(env_values={"MCP_CONFIG": str(config_path)}, mcp_config=initial)
            host, port = monitor.start("127.0.0.1", 0)
            try:
                payload = json.dumps({"server": "alpha", "policy": {"realtime_transport": "https", "permission_mode": "approval", "https_url": "https://new.example/mcp"}}).encode()
                with urllib_request.urlopen(urllib_request.Request(f"http://{host}:{port}/api/mcp-realtime-policy", data=payload, headers={"Content-Type": "application/json"}, method="POST"), timeout=2) as response:
                    result = json.loads(response.read().decode())
            finally:
                monitor.stop()
            self.assertEqual(result["policy"]["realtime_transport"], "https")
            saved = json.loads(config_path.read_text(encoding="utf-8"))["mcpServers"]["alpha"]
            self.assertEqual(saved["realtime"]["transport"], "native")
            self.assertEqual(saved["native"]["headers"]["Authorization"], "Bearer keep-secret")


if __name__ == "__main__":
    unittest.main()
