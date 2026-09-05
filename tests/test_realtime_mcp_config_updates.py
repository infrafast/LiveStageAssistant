import json
from pathlib import Path
import tempfile
import unittest

from voice_assistant.realtime.mcp_config import load_mcp_inventory, update_mcp_realtime_policy


class MCPConfigUpdateTests(unittest.TestCase):
    def _config(self, root: Path) -> Path:
        path = root / "mcp.json"
        path.write_text(json.dumps({
            "meta": {"keep": True},
            "mcpServers": {
                "alpha": {
                    "command": "node",
                    "args": ["alpha.js"],
                    "env": {"A": "1"},
                    "assistantOptions": {"routing": "alpha"},
                    "native": {"url": "https://alpha.test/mcp", "headers": {"X-A": "1"}},
                    "realtime": {"transport": "auto", "permissions": {"mode": "open"}},
                    "custom": {"preserve": 123},
                },
                "beta": {
                    "command": "node",
                    "args": ["beta.js"],
                    "env": {"B": "2"},
                },
            },
        }, indent=2), encoding="utf-8")
        return path

    def test_updates_only_target_server_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config(Path(tmp))
            before = json.loads(path.read_text(encoding="utf-8"))
            result = update_mcp_realtime_policy(
                path,
                "alpha",
                transport="native",
                permission_mode="restricted",
                allowed_tools=["one", "two", "one"],
            )
            after = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(result.realtime.transport, "native")
            self.assertEqual(result.realtime.permissions.mode, "restricted")
            self.assertEqual(result.realtime.permissions.allowed_tools, ("one", "two"))
            self.assertEqual(after["mcpServers"]["beta"], before["mcpServers"]["beta"])
            self.assertEqual(after["mcpServers"]["alpha"]["command"], "node")
            self.assertEqual(after["mcpServers"]["alpha"]["args"], ["alpha.js"])
            self.assertEqual(after["mcpServers"]["alpha"]["env"], {"A": "1"})
            self.assertEqual(after["mcpServers"]["alpha"]["assistantOptions"], {"routing": "alpha"})
            self.assertEqual(after["mcpServers"]["alpha"]["custom"], {"preserve": 123})
            self.assertEqual(after["meta"], {"keep": True})

    def test_open_mode_removes_stale_allowed_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config(Path(tmp))
            update_mcp_realtime_policy(
                path,
                "alpha",
                transport="stdio",
                permission_mode="open",
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            permissions = payload["mcpServers"]["alpha"]["realtime"]["permissions"]
            self.assertEqual(permissions, {"mode": "open"})

    def test_native_fields_change_only_when_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config(Path(tmp))
            update_mcp_realtime_policy(
                path,
                "alpha",
                transport="auto",
                permission_mode="approval",
                native_url="https://new.test/mcp",
                native_headers={"Authorization": "Bearer x"},
            )
            server = load_mcp_inventory(path)["alpha"]
            self.assertEqual(server.native.url, "https://new.test/mcp")
            self.assertEqual(server.native.headers, {"Authorization": "Bearer x"})
            self.assertEqual(server.realtime.permissions.mode, "approval")

    def test_rejects_invalid_restricted_policy_before_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config(Path(tmp))
            before = path.read_text(encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "requires allowedTools"):
                update_mcp_realtime_policy(
                    path,
                    "alpha",
                    transport="auto",
                    permission_mode="restricted",
                    allowed_tools=[],
                )
            self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_rejects_unknown_server_before_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config(Path(tmp))
            before = path.read_text(encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not found"):
                update_mcp_realtime_policy(path, "missing", transport="stdio", permission_mode="open")
            self.assertEqual(path.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
