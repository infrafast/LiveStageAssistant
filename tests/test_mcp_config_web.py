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
                        "permissions": {"mode": "restricted", "allowedTools": ["qlc_get_state"]},
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

    def test_runtime_tool_catalog_is_exposed_but_not_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config(Path(tmp))
            before = path.read_text(encoding="utf-8")
            policies = load_web_mcp_policies(path, discovered_tools={
                "mixer": [
                    {"name": "alpha"},
                    {"name": "beta"},
                    {"name": "alpha"},
                    "gamma",
                    {"description": "missing name"},
                ]
            })
            mixer = next(item for item in policies if item["name"] == "mixer")
            self.assertEqual(mixer["discovered_tools"], ["alpha", "beta", "gamma"])
            self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_update_preserves_secret_headers_and_unrelated_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config(Path(tmp))
            result = update_web_mcp_policy(path, "mixer", {
                "realtime_transport": "native",
                "permission_mode": "open",
                "native_url": "https://new.example.test/mcp",
                "allowed_tools": [],
            })
            self.assertEqual(result["realtime_transport"], "native")
            payload = json.loads(path.read_text(encoding="utf-8"))
            mixer = payload["mcpServers"]["mixer"]
            self.assertEqual(mixer["native"]["headers"]["Authorization"], "Bearer secret")
            self.assertEqual(mixer["assistantOptions"], {"routing": "mix"})
            self.assertEqual(payload["mcpServers"]["qlcplus"]["args"], ["qlc.js"])

    def test_restricted_mode_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config(Path(tmp))
            result = update_web_mcp_policy(path, "mixer", {
                "realtime_transport": "stdio",
                "permission_mode": "restricted",
                "allowed_tools": ["alpha", "beta", "alpha"],
            })
            self.assertEqual(result["allowed_tools"], ["alpha", "beta"])
            self.assertEqual(result["permission_mode"], "restricted")

    def test_invalid_allowed_tools_type_is_rejected_before_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config(Path(tmp))
            before = path.read_text(encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "allowed_tools"):
                update_web_mcp_policy(path, "mixer", {
                    "realtime_transport": "auto",
                    "permission_mode": "restricted",
                    "allowed_tools": "alpha",
                })
            self.assertEqual(path.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
