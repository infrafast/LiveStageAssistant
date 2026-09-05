import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voice_assistant.mcp_config_web import load_web_mcp_policies, update_web_mcp_policy


class MCPConfigWebTests(unittest.TestCase):
    def _config(self, root: Path) -> Path:
        path = root / "mcp.json"
        path.write_text(json.dumps({
            "mcpServers": {
                "mixer": {
                    "command": "node",
                    "args": ["mixer.js"],
                    "native": {
                        "url": "https://example.test/mcp",
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
                        "permissions": {"mode": "approval"},
                    },
                },
            }
        }, indent=2), encoding="utf-8")
        return path

    def test_load_payload_hides_header_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            policies = load_web_mcp_policies(self._config(Path(tmp)))
            mixer = next(item for item in policies if item["name"] == "mixer")
            self.assertEqual(mixer["native_url"], "https://example.test/mcp")
            self.assertTrue(mixer["native_headers_configured"])
            self.assertNotIn("headers", mixer)
            self.assertNotIn("Authorization", json.dumps(mixer))
            self.assertNotIn("secret", json.dumps(mixer))
            self.assertNotIn("allowed_tools", mixer)
            self.assertNotIn("discovered_tools", mixer)

    def test_update_preserves_secret_headers_and_unrelated_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config(Path(tmp))
            result = update_web_mcp_policy(path, "mixer", {
                "realtime_transport": "native",
                "permission_mode": "approval",
                "native_url": "https://new.example.test/mcp",
            })
            self.assertEqual(result["realtime_transport"], "native")
            self.assertEqual(result["permission_mode"], "approval")
            payload = json.loads(path.read_text(encoding="utf-8"))
            mixer = payload["mcpServers"]["mixer"]
            self.assertEqual(mixer["native"]["headers"]["Authorization"], "Bearer secret")
            self.assertEqual(mixer["assistantOptions"], {"routing": "mix"})
            self.assertEqual(payload["mcpServers"]["qlcplus"]["args"], ["qlc.js"])

    def test_open_mode_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config(Path(tmp))
            result = update_web_mcp_policy(path, "qlcplus", {
                "realtime_transport": "stdio",
                "permission_mode": "open",
            })
            self.assertEqual(result["permission_mode"], "open")

    def test_restricted_mode_is_rejected_before_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config(Path(tmp))
            before = path.read_text(encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "open.*approval"):
                update_web_mcp_policy(path, "mixer", {
                    "realtime_transport": "auto",
                    "permission_mode": "restricted",
                })
            self.assertEqual(path.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
