import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voice_assistant.mcp_config_web import (
    delete_web_mcp_server,
    load_web_mcp_policies,
    save_web_mcp_server,
    update_web_mcp_policy,
)


class MCPConfigWebTests(unittest.TestCase):
    def _config(self, root: Path) -> Path:
        path = root / "mcp.json"
        path.write_text(json.dumps({
            "mcpServers": {
                "mixer": {
                    "command": "node",
                    "args": ["mixer.js"],
                    "native": {"url": "https://example.test/mcp", "headers": {"Authorization": "Bearer secret"}},
                    "realtime": {"transport": "auto", "permissions": {"mode": "open"}},
                    "assistantOptions": {"routing": "mix"},
                },
                "qlcplus": {
                    "command": "node",
                    "args": ["qlc.js"],
                    "realtime": {"transport": "stdio", "permissions": {"mode": "open"}},
                },
            }
        }, indent=2), encoding="utf-8")
        return path

    def test_load_payload_uses_https_vocabulary_and_hides_header_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            policies = load_web_mcp_policies(self._config(Path(tmp)))
            mixer = next(item for item in policies if item["name"] == "mixer")
            self.assertEqual(mixer["https_url"], "https://example.test/mcp")
            self.assertEqual(mixer["realtime_transport"], "auto")
            self.assertEqual(mixer["command"], "node")
            self.assertEqual(mixer["args"], ["mixer.js"])
            self.assertTrue(mixer["auth_configured"])
            self.assertNotIn("headers", mixer)
            self.assertNotIn("secret", json.dumps(mixer))

    def test_https_update_maps_to_native_storage_and_preserves_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config(Path(tmp))
            result = update_web_mcp_policy(path, "mixer", {
                "realtime_transport": "https",
                "permission_mode": "approval",
                "https_url": "https://new.example.test/mcp",
            })
            self.assertEqual(result["realtime_transport"], "https")
            payload = json.loads(path.read_text(encoding="utf-8"))
            mixer = payload["mcpServers"]["mixer"]
            self.assertEqual(mixer["realtime"]["transport"], "native")
            self.assertEqual(mixer["native"]["headers"]["Authorization"], "Bearer secret")

    def test_stdio_approval_is_rejected_before_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config(Path(tmp))
            before = path.read_text(encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "approval.*STDIO"):
                update_web_mcp_policy(path, "qlcplus", {"realtime_transport": "stdio", "permission_mode": "approval"})
            self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_https_transport_requires_valid_https_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config(Path(tmp))
            with self.assertRaisesRegex(ValueError, "https://"):
                update_web_mcp_policy(path, "mixer", {"realtime_transport": "https", "permission_mode": "open", "https_url": "http://bad/mcp"})

    def test_create_update_delete_server_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config(Path(tmp))
            created = save_web_mcp_server(path, {
                "name": "new-mcp",
                "command": "python",
                "args": '["server.py"]',
                "realtime_transport": "auto",
                "permission_mode": "open",
                "https_url": "https://provider.example/mcp",
            })
            self.assertEqual(created["name"], "new-mcp")
            self.assertEqual(created["command"], "python")
            self.assertEqual(created["args"], ["server.py"])
            updated = save_web_mcp_server(path, {
                "name": "new-mcp",
                "local_url": "http://127.0.0.1:8787/mcp",
                "realtime_transport": "https",
                "permission_mode": "approval",
                "https_url": "https://provider.example/mcp",
            }, existing_name="new-mcp")
            self.assertEqual(updated["local_url"], "http://127.0.0.1:8787/mcp")
            self.assertEqual(updated["realtime_transport"], "https")
            deleted = delete_web_mcp_server(path, "new-mcp")
            self.assertEqual(deleted, "new-mcp")
            self.assertNotIn("new-mcp", json.loads(path.read_text(encoding="utf-8"))["mcpServers"])

    def test_update_preserves_existing_secret_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config(Path(tmp))
            save_web_mcp_server(path, {
                "name": "mixer",
                "command": "node",
                "args": '["mixer-v2.js"]',
                "realtime_transport": "https",
                "permission_mode": "open",
                "https_url": "https://new.example/mcp",
            }, existing_name="mixer")
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["mcpServers"]["mixer"]["native"]["headers"]["Authorization"], "Bearer secret")
            self.assertEqual(payload["mcpServers"]["mixer"]["args"], ["mixer-v2.js"])


if __name__ == "__main__":
    unittest.main()
