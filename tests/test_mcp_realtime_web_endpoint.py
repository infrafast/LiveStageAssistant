import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voice_assistant.mcp_realtime_web_endpoint import (
    mcp_config_path_from_snapshot,
    save_mcp_realtime_policy_from_snapshot,
)


class MCPRealtimeWebEndpointTests(unittest.TestCase):
    def _config(self, root: Path) -> Path:
        path = root / "mcp.json"
        path.write_text(json.dumps({
            "mcpServers": {
                "mixer": {
                    "command": "node",
                    "args": ["mixer.js"],
                    "native": {
                        "url": "https://old.example.test/mcp",
                        "headers": {"Authorization": "Bearer secret"},
                    },
                    "realtime": {
                        "transport": "auto",
                        "permissions": {"mode": "open"},
                    },
                    "assistantOptions": {"routing": "mix"},
                },
                "qlcplus": {
                    "command": "node",
                    "args": ["qlc.js"],
                    "realtime": {
                        "transport": "stdio",
                        "permissions": {"mode": "open"},
                    },
                },
            }
        }, indent=2), encoding="utf-8")
        return path

    def test_absolute_mcp_config_path_is_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config(Path(tmp)).resolve()
            snapshot = {"config": {"env": {"MCP_CONFIG": str(path)}}}
            self.assertEqual(mcp_config_path_from_snapshot(snapshot), path)

    def test_relative_mcp_config_path_uses_current_working_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self._config(root)
            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                snapshot = {"config": {"env": {"MCP_CONFIG": "mcp.json"}}}
                self.assertEqual(mcp_config_path_from_snapshot(snapshot), root / "mcp.json")
            finally:
                os.chdir(old_cwd)

    def test_save_updates_only_target_policy_and_preserves_secret_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config(Path(tmp))
            snapshot = {"config": {"env": {"MCP_CONFIG": str(path)}}}
            safe, refreshed = save_mcp_realtime_policy_from_snapshot(snapshot, "mixer", {
                "realtime_transport": "native",
                "permission_mode": "approval",
                "native_url": "https://new.example.test/mcp",
            })
            self.assertEqual(safe["realtime_transport"], "native")
            self.assertEqual(safe["permission_mode"], "approval")
            self.assertNotIn("headers", safe)
            mixer = refreshed["mcpServers"]["mixer"]
            self.assertEqual(mixer["native"]["headers"]["Authorization"], "Bearer secret")
            self.assertEqual(mixer["assistantOptions"], {"routing": "mix"})
            self.assertEqual(refreshed["mcpServers"]["qlcplus"]["args"], ["qlc.js"])

    def test_restricted_policy_is_rejected_without_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config(Path(tmp))
            before = path.read_text(encoding="utf-8")
            snapshot = {"config": {"env": {"MCP_CONFIG": str(path)}}}
            with self.assertRaisesRegex(ValueError, "open.*approval"):
                save_mcp_realtime_policy_from_snapshot(snapshot, "mixer", {
                    "realtime_transport": "auto",
                    "permission_mode": "restricted",
                })
            self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_missing_server_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config(Path(tmp))
            snapshot = {"config": {"env": {"MCP_CONFIG": str(path)}}}
            with self.assertRaisesRegex(ValueError, "not found"):
                save_mcp_realtime_policy_from_snapshot(snapshot, "missing", {
                    "realtime_transport": "stdio",
                    "permission_mode": "open",
                })


if __name__ == "__main__":
    unittest.main()
