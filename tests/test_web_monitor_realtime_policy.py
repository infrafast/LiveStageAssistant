from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from urllib import request as urllib_request

from voice_assistant.web_monitor import WebMonitor


class WebMonitorRealtimePolicyRouteTests(unittest.TestCase):
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
