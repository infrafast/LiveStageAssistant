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
                        "permissions": {"mode": "open"},
                    },
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
            self.assertTrue(mixer["auth_configured"])
            self.assertNotIn("headers", mixer)
            self.assertNotIn("Authorization", json.dumps(mixer))
            self.assertNotIn("secret", json.dumps(mixer))

    def test_https_update_maps_to_existing_native_storage_and_preserves_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config(Path(tmp))
            result = update_web_mcp_policy(path, "mixer", {
                "realtime_transport": "https",
                "permission_mode": "approval",
                "https_url": "https://new.example.test/mcp",
            })
            self.assertEqual(result["realtime_transport"], "https")
            self.assertEqual(result["permission_mode"], "approval")
            payload = json.loads(path.read_text(encoding="utf-8"))
            mixer = payload["mcpServers"]["mixer"]
            self.assertEqual(mixer["realtime"]["transport"], "native")
            self.assertEqual(mixer["native"]["url"], "https://new.example.test/mcp")
            self.assertEqual(mixer["native"]["headers"]["Authorization"], "Bearer secret")
            self.assertEqual(mixer["assistantOptions"], {"routing": "mix"})
            self.assertEqual(payload["mcpServers"]["qlcplus"]["args"], ["qlc.js"])

    def test_legacy_native_input_remains_accepted_but_returns_https(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config(Path(tmp))
            result = update_web_mcp_policy(path, "mixer", {
                "realtime_transport": "native",
                "permission_mode": "open",
                "native_url": "https://example.test/mcp",
            })
            self.assertEqual(result["realtime_transport"], "https")

    def test_stdio_open_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config(Path(tmp))
            result = update_web_mcp_policy(path, "qlcplus", {
                "realtime_transport": "stdio",
                "permission_mode": "open",
            })
            self.assertEqual(result["realtime_transport"], "stdio")
            self.assertEqual(result["permission_mode"], "open")

    def test_stdio_approval_is_rejected_before_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config(Path(tmp))
            before = path.read_text(encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "approval.*STDIO"):
                update_web_mcp_policy(path, "qlcplus", {
                    "realtime_transport": "stdio",
                    "permission_mode": "approval",
                })
            self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_https_transport_requires_valid_https_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config(Path(tmp))
            before = path.read_text(encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "https://"):
                update_web_mcp_policy(path, "mixer", {
                    "realtime_transport": "https",
                    "permission_mode": "open",
                    "https_url": "http://not-secure.example/mcp",
                })
            self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_invalid_permission_is_rejected_before_write(self):
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
