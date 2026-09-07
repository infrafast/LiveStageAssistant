from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib import request as urllib_request

from voice_assistant.web_monitor import WebMonitor, _runtime_service_tiles


class WebMonitorRealtimePolicyRouteTests(unittest.TestCase):
    def test_runtime_service_tiles_are_provider_and_mcp_neutral(self) -> None:
        tiles = _runtime_service_tiles(
            {
                "connectivity": "online",
                "engine": "openai-realtime",
                "provider": "openai",
                "model": "gpt-realtime-2.1",
                "voice": "marin",
                "ready": True,
                "mcp": [
                    {
                        "name": "alpha",
                        "configured_transport": "auto",
                        "effective_transport": "stdio",
                        "permission": "open",
                        "healthy": True,
                        "detail": "selected and healthy",
                    }
                ],
            }
        )
        self.assertEqual(tiles["Voice engine"]["status"], "ready")
        self.assertIn("openai-realtime", tiles["Voice engine"]["detail"])
        self.assertEqual(tiles["MCP · alpha"]["status"], "online")
        self.assertIn("transport=stdio", tiles["MCP · alpha"]["detail"])
        self.assertIn("configured=auto", tiles["MCP · alpha"]["detail"])
        self.assertIn("permission=open", tiles["MCP · alpha"]["detail"])

    def test_stale_runtime_status_forces_unhealthy_tiles(self) -> None:
        tiles = _runtime_service_tiles(
            {
                "engine": "openai-realtime",
                "provider": "openai",
                "model": "gpt-realtime-2.1",
                "ready": True,
                "mcp": [
                    {
                        "name": "alpha",
                        "configured_transport": "auto",
                        "effective_transport": "stdio",
                        "permission": "open",
                        "healthy": True,
                    }
                ],
            },
            stale=True,
        )
        self.assertEqual(tiles["Voice engine"]["status"], "offline")
        self.assertEqual(tiles["MCP · alpha"]["status"], "offline")
        self.assertIn("stale", tiles["Voice engine"]["detail"])

    def test_snapshot_includes_runtime_status_tiles_when_status_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            status_path = Path(temp_dir) / "runtime-status.json"
            status_path.write_text(
                json.dumps(
                    {
                        "connectivity": "online",
                        "engine": "classic",
                        "provider": "openai",
                        "model": "gpt-4.1-mini",
                        "voice": "",
                        "ready": True,
                        "profile": "/tmp/.env.online",
                        "mcp": [],
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"LSA_RUNTIME_STATUS_FILE": str(status_path)}):
                monitor = WebMonitor()
                snapshot = monitor.snapshot()
            self.assertEqual(snapshot["runtime_status"]["engine"], "classic")
            self.assertEqual(snapshot["services"]["Voice engine"]["status"], "ready")

    def test_runtime_status_endpoint_marks_old_file_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            status_path = Path(temp_dir) / "runtime-status.json"
            status_path.write_text(json.dumps({"engine": "classic", "ready": True, "mcp": []}), encoding="utf-8")
            old = time.time() - 120
            os.utime(status_path, (old, old))
            with patch.dict(
                os.environ,
                {
                    "LSA_RUNTIME_STATUS_FILE": str(status_path),
                    "LSA_RUNTIME_STATUS_STALE_SECONDS": "30",
                },
            ):
                monitor = WebMonitor()
                payload = monitor._runtime_status()
            self.assertTrue(payload["available"])
            self.assertTrue(payload["stale"])
            self.assertFalse(payload["ok"])

    def test_voice_engine_route_persists_realtime_model_and_voice_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env.online"
            env_path.write_text(
                "CONNECTIVITY_MODE=online\nVOICE_ENGINE=classic\nOPENAI_REALTIME_MODEL=old-model\nOPENAI_REALTIME_VOICE=old-voice\n",
                encoding="utf-8",
            )
            monitor = WebMonitor()
            monitor.update(
                env_values={
                    "CONNECTIVITY_MODE": "online",
                    "VOICE_ENGINE": "classic",
                    "OPENAI_REALTIME_MODEL": "old-model",
                    "OPENAI_REALTIME_VOICE": "old-voice",
                }
            )
            with patch.dict(os.environ, {"ASSISTANT_AUTO_ENV_DIR": temp_dir}):
                result = monitor._save_voice_engine(
                    "openai-realtime",
                    realtime_model="gpt-realtime-2.1",
                    realtime_voice="marin",
                )
            saved = env_path.read_text(encoding="utf-8")
            self.assertTrue(result["ok"])
            self.assertEqual(result["voice_engine"], "openai-realtime")
            self.assertEqual(result["realtime_model"], "gpt-realtime-2.1")
            self.assertEqual(result["realtime_voice"], "marin")
            self.assertIn("VOICE_ENGINE=openai-realtime", saved)
            self.assertIn("OPENAI_REALTIME_MODEL=gpt-realtime-2.1", saved)
            self.assertIn("OPENAI_REALTIME_VOICE=marin", saved)
            self.assertEqual(saved.count("VOICE_ENGINE="), 1)
            self.assertEqual(saved.count("OPENAI_REALTIME_MODEL="), 1)
            self.assertEqual(saved.count("OPENAI_REALTIME_VOICE="), 1)

    def test_post_updates_policy_and_preserves_native_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "mcp.json"
            config_path.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "alpha": {
                                "command": "node",
                                "args": ["server.js"],
                                "native": {
                                    "url": "https://old.example/mcp",
                                    "headers": {"Authorization": "Bearer keep-secret"},
                                },
                                "realtime": {
                                    "transport": "stdio",
                                    "permissions": {"mode": "open", "allowedTools": []},
                                },
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            initial = json.loads(config_path.read_text(encoding="utf-8"))
            monitor = WebMonitor()
            monitor.update(env_values={"MCP_CONFIG": str(config_path)}, mcp_config=initial)
            host, port = monitor.start("127.0.0.1", 0)
            try:
                payload = json.dumps(
                    {
                        "server": "alpha",
                        "policy": {
                            "realtime_transport": "native",
                            "permission_mode": "approval",
                            "native_url": "https://new.example/mcp",
                        },
                    }
                ).encode("utf-8")
                response = urllib_request.urlopen(
                    urllib_request.Request(
                        f"http://{host}:{port}/api/mcp-realtime-policy",
                        data=payload,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    ),
                    timeout=2,
                )
                result = json.loads(response.read().decode("utf-8"))
            finally:
                monitor.stop()

            self.assertTrue(result["ok"])
            self.assertEqual(result["policy"]["realtime_transport"], "native")
            self.assertEqual(result["policy"]["permission_mode"], "approval")
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            server = saved["mcpServers"]["alpha"]
            self.assertEqual(server["native"]["url"], "https://new.example/mcp")
            self.assertEqual(server["native"]["headers"]["Authorization"], "Bearer keep-secret")
            self.assertEqual(server["realtime"]["transport"], "native")
            self.assertEqual(server["realtime"]["permissions"]["mode"], "approval")
            snapshot_server = monitor.snapshot()["config"]["mcp"]["mcpServers"]["alpha"]
            self.assertEqual(snapshot_server["native"]["headers"]["Authorization"], "***redacted***")


if __name__ == "__main__":
    unittest.main()
